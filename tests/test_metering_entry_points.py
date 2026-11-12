"""Every model entry point binds the company, the trace and the job before its first call.

Each test drives a real entry point (the worker's customer turn, the workspace assistant, the HR
assistant, the training summary, the Email tab and the batch drafter) with a provider double that
does what ``GroqClient`` and ``GeminiClient`` do around a call: require a bound context before the
request, then book the usage through a real ``LedgerMeter``. The rows are read back from the
disposable Docker Postgres. The LangGraph test needs no database. All data is synthetic.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from apps.worker.main import IngressWorker
from csa.contracts.state import Draft, Signals, TurnState
from csa.core import db
from csa.decide.completion import CompletionError
from csa.decide.groq import GroqError
from csa.graph.coordinator import build_graph, build_runtime, run_turn
from csa.graph.events import PostgresEventSink
from csa.graph.saver import build_checkpoint_pool, build_saver
from csa.hr.assistant import HrAssistant, HrChatPlan
from csa.metering.context import MeterContext, current_context, meter_context
from csa.metering.meter import LedgerMeter, Usage, UsageMeter
from csa.turn.deps import PostgresTurnDependencies, build_llm_provider
from csa.workspace.email_batch import DraftReview, EmailDecision
from csa.workspace.planner import WorkspacePlanner
from langgraph.checkpoint.memory import InMemorySaver
from psycopg.rows import dict_row

from tests.conftest import owner_dsn_for, requires_docker
from tests.test_console_sessions import SessionWorld
from tests.test_coordinator import FakeDependencies, make_envelope
from tests.test_email_batch import BatchModel, _build, _expanded, _ready
from tests.test_email_batch_recipients import _customer, _subscription
from tests.test_email_service import (  # noqa: F401 - fixture import
    COMPOSE,
    ScriptedModel,
    World,
    _employee,
    _enable_and_connect,
    world,
)
from tests.test_training_api import enable
from tests.test_training_summary import FakeModel, ask, deck
from tests.test_worker import _configure_test_settings, _seed_events
from tests.test_workspace_planner import (
    SEEDED_EMAIL,
    ScriptedCompletion,
    _mail_ready,
    _thread,
    _turn,
)
from tests.test_workspace_registry import (  # noqa: F401 - fixture import
    _hr_member,
    proposals_app,
)

pytest_plugins = ["tests.test_console_sessions"]


class Metered:
    """A provider double with the real clients' metering: context first, usage after the reply.

    The usage is booked whether the reply then succeeds or fails, as a billed response is.
    """

    def __init__(self, inner: Any, meter: UsageMeter) -> None:
        self.inner = inner
        self.meter = meter
        self.seen: list[MeterContext] = []

    @property
    def model(self) -> str:
        return str(self.inner.model)

    async def json_completion(self, schema: type[Any], **kwargs: Any) -> Any:
        self.meter.require_context()
        self.seen.append(current_context())
        try:
            return await self.inner.json_completion(schema, **kwargs)
        finally:
            await self.meter.record(
                Usage(
                    provider="synthetic",
                    model=self.model,
                    prompt_tokens=100,
                    completion_tokens=20,
                    schema_name=schema.__name__,
                )
            )


def _ledger(dsn: str, client_id: str | UUID) -> list[dict[str, Any]]:
    """The company's ledger rows in write order (read as the owner, past row security)."""
    with psycopg.connect(dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT client_id, trace_id, conversation_id, job, schema_name FROM ai_usage_events"
            " WHERE client_id = %s ORDER BY id",
            (client_id,),
        )
        return cur.fetchall()


# --------------------------------------------------------------------------- LangGraph


class Watching(FakeDependencies):
    """Coordinator seams that read the bound context inside the nodes, fan-out included."""

    def __init__(self) -> None:
        super().__init__(parallel=True)
        self.seen: dict[str, list[MeterContext]] = {}

    def _see(self, node: str) -> None:
        self.seen.setdefault(node, []).append(current_context())

    async def signals(self, envelope: Any, context: Any) -> Signals:
        self._see("signals")
        return await super().signals(envelope, context)

    async def execute_spoke(self, task: Any, envelope: Any, context: Any) -> Any:
        self._see("run_spoke")
        return await super().execute_spoke(task, envelope, context)

    async def compose(self, state: TurnState) -> Draft:
        self._see("compose")
        return await super().compose(state)


class NoEvents:
    async def record(self, **_: Any) -> None:
        return None


@pytest.mark.parametrize("traced", [False, True], ids=["plain", "traced"])
async def test_langgraph_nodes_see_bound_context(traced: bool) -> None:
    deps = Watching()
    saver = InMemorySaver()
    graph = build_graph(deps, checkpointer=saver, events=NoEvents() if traced else None)
    envelope = make_envelope("hello")
    bound = MeterContext(
        client_id=envelope.client_id,
        trace_id=envelope.trace_id,
        job="customer_turn",
        conversation_id=envelope.conversation.id,
    )
    with meter_context(bound):
        state = await run_turn(envelope, deps, checkpointer=saver, graph=graph)
    assert state.node_error is None and len(state.outbound) == 1
    # Both parallel spokes (Send fan-out) and the sequential nodes ran inside the bound context.
    assert {node: len(seen) for node, seen in deps.seen.items()} == {
        "signals": 1,
        "run_spoke": 2,
        "compose": 1,
    }
    assert all(ctx is bound for seen in deps.seen.values() for ctx in seen)
    assert deps.fallbacks == []
    # The same graph run with nothing bound: the first node that reads the context fails.
    unbound = Watching()
    graph = build_graph(unbound, checkpointer=InMemorySaver())
    await run_turn(make_envelope("hello"), unbound, graph=graph)
    assert unbound.fallbacks == ["signals:MeteringContextMissing"]


# --------------------------------------------------------------------------- customer turn


class Died(BaseException):
    """Process death; graph nodes only catch Exception."""


class Refusing:
    """A provider whose reply never validates (still billed), or that dies mid-call."""

    model = "synthetic-refusing"

    def __init__(self, *, die: bool = False) -> None:
        self.die = die

    async def json_completion(self, schema: type[Any], **_: Any) -> Any:
        if self.die:
            raise Died
        raise CompletionError("groq_invalid_output")


async def _turn_worker(
    owner_dsn: str, app_dsn: str, *, die: bool = False
) -> tuple[list[Any], IngressWorker]:
    """The production wiring: the real worker, compiled graph, event sink and turn seams,
    with a primary and a second provider that both refuse (so the call falls back once)."""
    owner_pool = db.build_pool(owner_dsn, max_size=4)
    app_pool = db.build_pool(app_dsn, max_size=4)
    checkpoint_pool = build_checkpoint_pool(app_dsn)
    for pool in (owner_pool, app_pool, checkpoint_pool):
        await pool.open(wait=True)
    meter = LedgerMeter(app_pool)
    llm = build_llm_provider(
        Metered(Refusing(die=die), meter),  # type: ignore[arg-type]
        compose_fallback=Metered(Refusing(), meter),  # type: ignore[arg-type]
    )
    deps = PostgresTurnDependencies(app_pool, llm)
    runtime = build_runtime(deps, build_saver(checkpoint_pool), PostgresEventSink(app_pool))
    worker = IngressWorker(owner_pool, app_pool, lock_conninfo=owner_dsn, coordinator=runtime)
    return [owner_pool, app_pool, checkpoint_pool], worker


async def _close(pools: list[Any]) -> None:
    for pool in pools:
        await pool.close()


def _event(owner_dsn: str, event_id: UUID) -> tuple[UUID, UUID]:
    with psycopg.connect(owner_dsn) as conn:
        row = conn.execute(
            "SELECT trace_id, (raw->'envelope'->'conversation'->>'id')::uuid FROM ingress_events"
            " WHERE id = %s",
            (event_id,),
        ).fetchone()
    assert row is not None
    return row[0], row[1]


def _requeue(owner_dsn: str, event_id: UUID) -> None:
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE ingress_events SET status='received', completed_at=NULL, "
            "next_attempt_at=now() WHERE id=%s",
            (event_id,),
        )


def _status(owner_dsn: str, event_id: UUID) -> str:
    with psycopg.connect(owner_dsn) as conn:
        row = conn.execute("SELECT status FROM ingress_events WHERE id=%s", (event_id,)).fetchone()
    assert row is not None
    return str(row[0])


@requires_docker
@pytest.mark.usefixtures("lg_setup_dsn")
async def test_customer_turn_books_both_providers_to_the_envelope_trace(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, [event_id] = _seed_events(owner_dsn)
    trace, conversation = _event(owner_dsn, event_id)
    pools, worker = await _turn_worker(owner_dsn, app_dsn)
    try:
        assert await worker.process_once() is True
    finally:
        await _close(pools)
    assert _status(owner_dsn, event_id) == "completed"  # the refusals ended in the fail-safe
    # The classifier's primary call and its fallback are one execution under the event's trace.
    assert (
        _ledger(owner_dsn, client_id)
        == [
            {
                "client_id": UUID(client_id),
                "trace_id": trace,
                "conversation_id": conversation,
                "job": "customer_turn",
                "schema_name": "Classification",
            }
        ]
        * 2
    )


@requires_docker
@pytest.mark.usefixtures("lg_setup_dsn")
async def test_redelivered_turn_reuses_trace(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, [event_id] = _seed_events(owner_dsn)
    trace, _ = _event(owner_dsn, event_id)
    # The first delivery dies inside the classifier call, after the usage was booked.
    pools, worker = await _turn_worker(owner_dsn, app_dsn, die=True)
    try:
        with pytest.raises(Died):
            await worker.process_once()
    finally:
        await _close(pools)
    assert len(_ledger(owner_dsn, client_id)) == 1
    # The reaper hands the same event back; the turn resumes and calls the model again.
    _requeue(owner_dsn, event_id)
    pools, worker = await _turn_worker(owner_dsn, app_dsn)
    try:
        assert await worker.process_once() is True
        assert _status(owner_dsn, event_id) == "completed"
        rows = _ledger(owner_dsn, client_id)
        assert len(rows) == 3
        # A finished turn delivered once more runs nothing and books nothing.
        _requeue(owner_dsn, event_id)
        assert await worker.process_once() is True
    finally:
        await _close(pools)
    rows = _ledger(owner_dsn, client_id)
    assert len(rows) == 3
    assert {row["trace_id"] for row in rows} == {trace}, "one execution, however often delivered"
    assert {row["job"] for row in rows} == {"customer_turn"}


# --------------------------------------------------------------------------- workspace assistant


@requires_docker
async def test_workspace_turn_that_drafts_an_email_is_one_execution(
    proposals_app: tuple[SessionWorld, Any],  # noqa: F811
    migrated_dsn: str,
) -> None:
    w, _ = proposals_app
    meter = LedgerMeter(w.pool)
    planner_model = ScriptedCompletion()
    w.app.state.workspace_planner = WorkspacePlanner(Metered(planner_model, meter))  # type: ignore[arg-type]
    email = w.app.state.email_service
    email.model = email.batch_model = Metered(ScriptedModel(), meter)
    tenant = w.tenants[0]
    browser = await _mail_ready(w, "hr")
    ada = uuid4()
    await w.q(
        "INSERT INTO hr_employees(client_id,id,display_name,work_email) VALUES (%s,%s,%s,%s)",
        tenant, ada, "Ada Directory", SEEDED_EMAIL,
    )  # fmt: skip
    thread = await _thread(w, browser)
    planner_model.reply(
        {
            "action": "email_compose",
            "recipient_employee_id": str(ada),
            "subject": "Leave next week",
            "instructions": "Ask to take Friday off",
        }
    )
    key = str(uuid4())
    asked = f"Email employee {ada} about leave"
    turn = await _turn(w, browser, thread, asked, "email", key=key)
    assert turn.status_code == 201, turn.text
    planned, drafted = _ledger(migrated_dsn, tenant)
    # The draft EmailService wrote inherits the request's binding: one execution, one job.
    assert (planned["job"], planned["schema_name"]) == ("workspace_assistant", "PlannerOutput")
    assert (drafted["job"], drafted["schema_name"]) == ("workspace_assistant", "DraftProposal")
    assert planned["trace_id"] == drafted["trace_id"]
    assert planned["conversation_id"] is None and drafted["conversation_id"] is None
    # A replayed turn re-plans nothing and books nothing.
    again = await _turn(w, browser, thread, asked, "email", key=key)
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert len(_ledger(migrated_dsn, tenant)) == 2


# --------------------------------------------------------------------------- HR assistant


class HrPlanModel:
    """The HR planner's model: fails once when told to, then asks for details."""

    model = "synthetic-hr"

    def __init__(self) -> None:
        self.fail_next = False

    async def json_completion(self, schema: type[Any], **_: Any) -> Any:
        if self.fail_next:
            self.fail_next = False
            raise GroqError("groq_timeout")
        assert schema is HrChatPlan
        return HrChatPlan(clarification="request_details")


async def _ask_hr(w: SessionWorld, browser: Any, key: UUID) -> Any:
    return await browser.post(
        "/console/v1/hr/assistant",
        json={"message": "Prepare a training workshop"},
        headers={**w.headers(browser), "Idempotency-Key": str(key)},
    )


@requires_docker
async def test_hr_assistant_trace_follows_the_idempotency_key(
    proposals_app: tuple[SessionWorld, Any],  # noqa: F811
    migrated_dsn: str,
) -> None:
    w, _ = proposals_app
    browser = await _hr_member(w)
    tenant = w.tenants[0]
    model = HrPlanModel()
    w.app.state.hr_assistant = HrAssistant(
        w.app.state.hr_service,
        Metered(model, LedgerMeter(w.pool)),  # type: ignore[arg-type]
    )
    key = uuid4()
    model.fail_next = True
    failed = await _ask_hr(w, browser, key)
    assert failed.status_code == 503, failed.text
    # The retry of the same request is the same execution: the plan was never stored.
    retried = await _ask_hr(w, browser, key)
    assert retried.status_code == 200, retried.text
    replayed = await _ask_hr(w, browser, key)  # the stored plan: no model call
    assert replayed.status_code == 200 and replayed.json() == retried.json()
    other = await _ask_hr(w, browser, uuid4())
    assert other.status_code == 200, other.text
    first, second, third = _ledger(migrated_dsn, tenant)
    assert {row["job"] for row in (first, second, third)} == {"hr_assistant"}
    assert first["trace_id"] == second["trace_id"] != third["trace_id"]
    assert first["trace_id"] != key  # derived from the key, never the key itself


# --------------------------------------------------------------------------- training summary


@requires_docker
async def test_training_summary_books_one_execution(
    sessions: SessionWorld, migrated_dsn: str
) -> None:
    model = Metered(FakeModel(), LedgerMeter(sessions.pool))
    sessions.app.state.training_model = model
    tenant = await enable(sessions)
    item = await deck(sessions)
    staff, _ = await sessions.member()
    made = await ask(sessions, staff, item)
    assert made.status_code == 200, made.text
    assert (await ask(sessions, staff, item)).status_code == 200  # stored: no model call
    [row] = _ledger(migrated_dsn, tenant)
    assert (row["job"], row["schema_name"]) == ("training_summary", "SummaryDraft")
    assert row["conversation_id"] is None


# --------------------------------------------------------------------------- Email tab


def _meter_email(w: World) -> Metered:
    service = w.app.state.email_service
    metered = Metered(w.model, LedgerMeter(w.app.state.pool))
    service.model = service.batch_model = metered
    return metered


@requires_docker
async def test_email_tab_jobs_each_book_their_own_execution(
    world: World,  # noqa: F811
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _meter_email(world)
    await _enable_and_connect(world, monkeypatch)
    synced = await world.call("staff", "POST", "/console/v1/email/sync")
    assert synced.status_code == 200, synced.text
    analysed = await world.call("staff", "POST", "/console/v1/email/threads/t1/analyse")
    assert analysed.status_code == 200, analysed.text
    drafted = await world.call(
        "staff", "POST", "/console/v1/email/threads/t1/draft", {"instructions": "Say tomorrow"}
    )
    assert drafted.status_code == 201, drafted.text
    replied = await world.call(
        "staff", "POST", "/console/v1/email/threads/t1/reply", {"instructions": "Say tomorrow"}
    )
    assert replied.status_code == 201, replied.text
    boss = await _employee(world, "aisyah@example.test")
    composed = await world.call(
        "staff",
        "POST",
        COMPOSE,
        {"recipient_employee_id": str(boss), "subject": "Leave", "instructions": "Ask for Friday"},
    )
    assert composed.status_code == 201, composed.text
    rows = _ledger(migrated_dsn, world.tenant)
    assert [(row["job"], row["schema_name"]) for row in rows] == [
        ("email_analysis", "ThreadAnalysis"),
        ("email_reply", "DraftProposal"),
        ("email_reply", "DraftProposal"),
        ("email_compose", "DraftProposal"),
    ]
    assert len({row["trace_id"] for row in rows}) == 4, "each request is its own execution"


@requires_docker
async def test_attention_books_each_thread_analysis_as_its_own_execution(
    world: World,  # noqa: F811
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metered = _meter_email(world)
    await _enable_and_connect(world, monkeypatch)
    assert (await world.call("staff", "POST", "/console/v1/email/sync")).status_code == 200
    attention = await world.call("staff", "GET", "/console/v1/email/attention")
    assert attention.status_code == 200, attention.text
    rows = _ledger(migrated_dsn, world.tenant)
    assert len(rows) == len(metered.seen) >= 2
    assert {row["job"] for row in rows} == {"email_analysis"}
    # No enclosing context: each thread's analysis is one execution with its own trace.
    assert len({row["trace_id"] for row in rows}) == len(rows)


@requires_docker
async def test_two_direct_analyses_are_two_executions(
    world: World,  # noqa: F811
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _meter_email(world)
    await _enable_and_connect(world, monkeypatch)
    assert (await world.call("staff", "POST", "/console/v1/email/sync")).status_code == 200
    for thread in ("t1", "t1"):  # the same thread twice, two requests
        analysed = await world.call("staff", "POST", f"/console/v1/email/threads/{thread}/analyse")
        assert analysed.status_code == 200, analysed.text
    first, second = _ledger(migrated_dsn, world.tenant)
    assert first["job"] == second["job"] == "email_analysis"
    assert first["trace_id"] != second["trace_id"]


# --------------------------------------------------------------------------- batch drafting


@requires_docker
async def test_batch_review_inherits_compose_job(
    world: World,  # noqa: F811
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = BatchModel(
        lambda user: "Hi,\n\nYour plan includes free delivery.\n\nThe team",
        review=DraftReview(ok=False, unsupported=["Your plan includes free delivery."]),
    )
    metered = Metered(model, LedgerMeter(world.app.state.pool))
    await _ready(world, monkeypatch, metered)  # type: ignore[arg-type]
    contact = await _customer(world, "Fact Customer", "facts@example.test")
    await _subscription(world, contact, renews_in_days=9)
    expanded = await _expanded(
        world,
        [
            {"kind": "customer", "value": "Fact Customer"},
            {"kind": "address", "value": "walkin@example.test"},
        ],
    )
    decision = EmailDecision(mode="personalised", sources=frozenset({"subscription"}), send=True)
    await _build(world, expanded, decision)
    rows = _ledger(migrated_dsn, world.tenant)
    assert sorted(row["schema_name"] for row in rows) == ["DraftProposal"] * 2 + ["DraftReview"]
    assert {row["job"] for row in rows} == {"email_compose"}
    [review] = [row for row in rows if row["schema_name"] == "DraftReview"]
    drafts = [row for row in rows if row["schema_name"] == "DraftProposal"]
    # The review ran inside its draft's own execution, not a context of its own.
    assert sum(row["trace_id"] == review["trace_id"] for row in drafts) == 1
    assert len({row["trace_id"] for row in drafts}) == 2, "each recipient's draft is one execution"
    # Seen from inside the calls: the reviewer's context is the very one its draft bound.
    calls = zip(metered.seen, model.calls, strict=True)
    [review_ctx] = [ctx for ctx, call in calls if call[0] == "DraftReview"]
    assert review_ctx.trace_id == review["trace_id"] and review_ctx.job == "email_compose"


@requires_docker
async def test_batch_inside_an_assistant_request_is_that_one_execution(
    world: World,  # noqa: F811
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = BatchModel(
        lambda user: "Hi,\n\nYour plan includes free delivery.\n\nThe team",
        review=DraftReview(ok=False, unsupported=["Your plan includes free delivery."]),
    )
    await _ready(world, monkeypatch, Metered(model, LedgerMeter(world.app.state.pool)))  # type: ignore[arg-type]
    for i in range(3):
        contact = await _customer(world, f"Batch Customer {i}", f"batch{i}@example.test")
        await _subscription(world, contact, renews_in_days=9)
    expanded = await _expanded(world, [{"kind": "group", "group": "active_subscribers"}])
    decision = EmailDecision(mode="personalised", sources=frozenset({"subscription"}), send=True)
    request = MeterContext(
        client_id=UUID(str(world.tenant)), trace_id=uuid4(), job="workspace_assistant"
    )
    # create_planned_turn binds this around the plan; _batch_step runs the drafter inside it.
    with meter_context(request):
        await _build(world, expanded, decision)
    rows = _ledger(migrated_dsn, world.tenant)
    assert sorted(row["schema_name"] for row in rows) == ["DraftProposal"] * 3 + ["DraftReview"] * 3
    assert {(row["job"], row["trace_id"]) for row in rows} == {
        ("workspace_assistant", request.trace_id)
    }
