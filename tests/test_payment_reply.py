"""The reply of a payment turn is the payment agent's facts as they stand, never model text.

An account number, a reference and an amount reach the customer exactly as the database holds
them: the composer is not asked (`composer="template"`), so the coordinator's check is grounding
alone. A handoff still wins, and every other turn is composed as before.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from csa.contracts.envelope import Contact, Envelope
from csa.contracts.facts import AgentError, AgentResult, AgentResultData, Fact, ResultMeta
from csa.contracts.plan import Intent, Plan, RiskClass, Task
from csa.contracts.state import Context, Draft, Handoff, Signals, TurnState, Verification
from csa.core.pii import redact
from csa.decide.llm import PlannedTask, PlanOut, StepOut
from csa.graph.coordinator import run_turn
from csa.graph.policy.gate import Policy
from csa.turn import deps
from csa.turn.deps import PostgresTurnDependencies, build_llm_provider, payment_facts
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_bookings_bot import Shop, shop
from tests.test_coordinator import FakeDependencies, make_envelope, make_signals
from tests.test_customer_replies import activate_only
from tests.test_llm_provider import _ok, _provider
from tests.test_payment_invoices import Ledger

pytest_plugins = ["tests.test_console_sessions"]
__all__ = ["shop"]  # the fixture, imported so the whole turns below can use it

pytestmark = pytest.mark.asyncio

ACCOUNT = "512000000017"
DUE = datetime(2031, 3, 20, 4, 0, tzinfo=UTC)
FACTS = [
    ("payment_invoice_1", "Invoice ABCD-EFG: MYR 1,059.90 for Basic, due 20 March 2031."),
    (
        "payment_method_1",
        f"Bank transfer: Bank Sintetik, account {ACCOUNT}, name Kedai Contoh Sdn Bhd.",
    ),
]


# --- without a database: compose and verify -------------------------------------------------------


class Model:
    """A model transport that records each call and answers with `payload` (or a 500)."""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.content.decode())
        if self.payload is None:
            return httpx.Response(500)
        return _ok(self.payload)(request)


def _deps(model: Model, *, semantic_verify: bool = False) -> PostgresTurnDependencies:
    made = PostgresTurnDependencies(None, _provider(model))  # type: ignore[arg-type]

    async def policy(_client_id: UUID) -> Policy:
        return Policy(semantic_verify=semantic_verify)

    made._policy = policy  # type: ignore[method-assign]
    return made


def _result(task_id: str, agent: str, facts: list[tuple[str, str]], *, ok: bool = True) -> Any:
    return AgentResult(
        ok=ok,
        status="ok" if ok else "error",
        task_id=task_id,
        agent=agent,
        data=AgentResultData(
            facts=[Fact(id=fact_id, text=text, source=agent) for fact_id, text in facts]
        ),
        error=None
        if ok
        else AgentError(code="disabled", category="validation", message="Payments disabled."),
        meta=ResultMeta(trace_id=uuid4(), latency_ms=0),
    )


def _state(
    results: list[AgentResult],
    *,
    body: str = "how do I pay?",
    handoff: bool = False,
    strategy: str = "auto_reply",
    draft: Draft | None = None,
    actions: dict[str, str] | None = None,
) -> TurnState:
    planned = actions or {}
    tasks = [
        Task(task_id=r.task_id, agent=r.agent, input={"action": planned.get(r.task_id, "x")})
        for r in results
    ]
    facts = [
        fact.model_copy(update={"id": f"{r.task_id}:{fact.id}"})
        for r in results
        for fact in r.facts
    ]
    return TurnState(
        envelope=make_envelope(body),
        ctx=Context(),
        signals=make_signals(Intent.BILLING_INVOICE, answers={"language": "en"}),
        plan=Plan(
            tasks=tasks,
            reply_strategy=strategy,  # type: ignore[arg-type]
            intent=Intent.BILLING_INVOICE,
            confidence=1,
            source="llm",
        ),
        results=results,
        facts=facts,
        handoff=Handoff(required=True, reason="human_requested", priority="high")
        if handoff
        else None,
        route="hard_escalate" if handoff else None,
        draft=draft,
    )


async def test_payment_facts_are_the_ok_payment_tasks_facts_with_turn_ids() -> None:
    state = _state(
        [
            _result("t0-payment_agent", "payment_agent", FACTS),
            _result("t1-kb_agent", "kb_agent", [("kb_1", "We open at 9.")]),
            _result("t2-payment_agent", "payment_agent", [("x", "no")], ok=False),
        ]
    )
    assert [(fact_id, fact.text) for fact_id, fact in payment_facts(state)] == [
        (f"t0-payment_agent:{fact_id}", text) for fact_id, text in FACTS
    ]
    assert payment_facts(_state([_result("t1-kb_agent", "kb_agent", [("k", "x")])])) == []


async def test_a_payment_turn_replies_with_the_facts_and_never_asks_the_model() -> None:
    model = Model({"reply": "Pay to account 512000000099.", "used_fact_ids": []})
    made = _deps(model, semantic_verify=True)
    state = _state([_result("t0-payment_agent", "payment_agent", FACTS)])

    draft = await made.compose(state)

    assert draft == Draft(
        text="\n".join(text for _, text in FACTS),
        language="en",
        fact_ids=[f"t0-payment_agent:{fact_id}" for fact_id, _ in FACTS],
        composer="template",
    )
    # The facts' grounding is the whole check: no review, even with the semantic check on.
    assert await made.verify(state.model_copy(update={"draft": draft})) == Verification(ok=True)
    assert model.calls == []


async def test_a_handoff_still_wins_over_the_payment_facts() -> None:
    model = Model({"reply": "A team member will reply here.", "staff_note": None})
    state = _state([_result("t0-payment_agent", "payment_agent", FACTS)], handoff=True)
    draft = await _deps(model)._compose(state)
    assert draft.handoff is True and ACCOUNT not in draft.text
    composed = await _deps(model).compose(state)
    assert composed.handoff is True and ACCOUNT not in composed.text

    escalating = _state([_result("t0-payment_agent", "payment_agent", FACTS)], strategy="escalate")
    assert (await _deps(Model(None)).compose(escalating)).handoff is True


async def test_a_turn_without_payment_tasks_is_composed_as_before() -> None:
    model = Model({"reply": "We open at 9.", "used_fact_ids": ["t1-kb_agent:kb_1"]})
    state = _state([_result("t1-kb_agent", "kb_agent", [("kb_1", "We open at 9 every day.")])])
    draft = await _deps(model).compose(state)
    assert draft.text == "We open at 9." and draft.composer == "primary"
    assert len(model.calls) == 1
    # A failed payment task alone gives no facts to send: the model writes the reply as before.
    failed = _state([_result("t0-payment_agent", "payment_agent", [], ok=False)])
    again = await _deps(model).compose(failed)
    assert again.composer == "primary" and len(model.calls) == 2


BOOKED = ("booking_booked_1", "Booked: Haircut on Tuesday 17 March 2031 at 15:00.")
TICKET = ("ticket_created_1", "Ticket TCK-2031 is open: card payment refused.")
SIGNUP = ("subscription_signup_1", "Your sign-up request for Basic is with the team.")


@pytest.mark.parametrize(
    ("agent", "action", "fact"),
    [
        ("booking_agent", "book", BOOKED),
        ("ticket_agent", "create", TICKET),
        ("subscription_agent", "request_signup", SIGNUP),
    ],
)
async def test_a_payment_reply_keeps_what_the_turn_wrote(
    agent: str, action: str, fact: tuple[str, str]
) -> None:
    # "Book me Tuesday 3pm, and how do I pay?": the write has committed, so the customer is told
    # it, then how to pay; still the facts as they stand, and the model is never asked.
    model = Model({"reply": "Pay to account 512000000099.", "used_fact_ids": []})
    made = _deps(model, semantic_verify=True)
    write_id = f"t0-{agent}"
    state = _state(
        [
            _result(write_id, agent, [fact]),
            _result("t1-payment_agent", "payment_agent", FACTS),
        ],
        actions={write_id: action, "t1-payment_agent": "instructions"},
    )

    draft = await made.compose(state)

    assert draft == Draft(
        text="\n".join([fact[1], *(text for _, text in FACTS)]),
        language="en",
        fact_ids=[f"{write_id}:{fact[0]}", *(f"t1-payment_agent:{i}" for i, _ in FACTS)],
        composer="template",
    )
    assert f"account {ACCOUNT}," in draft.text
    assert await made.verify(state.model_copy(update={"draft": draft})) == Verification(ok=True)
    assert model.calls == []


async def test_a_payment_reply_still_leaves_out_other_tasks_reads() -> None:
    # Reads of other tasks (and a write that failed, which has no facts) stay out, as before.
    model = Model({"reply": "We open at 9.", "used_fact_ids": []})
    reads = _state(
        [
            _result("t0-kb_agent", "kb_agent", [("kb_1", "We open at 9 every day.")]),
            _result("t1-booking_agent", "booking_agent", [("slot_1", "Tuesday 15:00 is free.")]),
            _result("t2-payment_agent", "payment_agent", FACTS),
        ],
        actions={"t1-booking_agent": "availability", "t2-payment_agent": "instructions"},
    )
    failed = _state(
        [
            _result("t0-ticket_agent", "ticket_agent", [("ticket_1", "TCK-2031 is open.")]),
            _result("t1-booking_agent", "booking_agent", [], ok=False),
            _result("t2-payment_agent", "payment_agent", FACTS),
        ],
        actions={
            "t0-ticket_agent": "get",
            "t1-booking_agent": "book",
            "t2-payment_agent": "instructions",
        },
    )

    for state in (reads, failed):
        draft = await _deps(model).compose(state)
        assert draft.text == "\n".join(text for _, text in FACTS)
        assert draft.fact_ids == [f"t2-payment_agent:{fact_id}" for fact_id, _ in FACTS]
        assert draft.composer == "template"
    assert model.calls == []


async def test_grounding_refuses_an_invented_account_number() -> None:
    state = _state(
        [_result("t1-kb_agent", "kb_agent", [("kb_1", "We take bank transfers.")])],
        body="where do I transfer?",
        draft=Draft(text="Transfer to account 512000000099 please.", language="en"),
    )
    verdict = await _deps(Model(None)).verify(state)
    assert verdict.ok is False
    assert verdict.issues == ["ungrounded value 512000000099"]


# --- whole turns against the database ----------------------------------------------------------


class PaymentModel:
    """A fake model: plans one payment task and refuses to write or review a reply."""

    model = "scripted"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.asked: list[str] = []

    async def json_completion(self, schema: Any, *, system: str, user: str, **_: Any) -> Any:
        self.asked.append(schema.__name__)
        if schema is PlanOut:
            planned = PlannedTask(agent="payment_agent", input=self.payload)
            return PlanOut(tasks=[planned], reply_strategy="auto_reply")
        if schema is StepOut:
            return StepOut(done=True)
        raise AssertionError(f"{schema.__name__} must not be asked on a payment turn")


class PaymentTurn(FakeDependencies):
    """The coordinator's seams for one payment turn, with the real agent, planner and composer."""

    known_agents = frozenset({"payment_agent", "escalation_agent"})

    def __init__(self, real: PostgresTurnDependencies) -> None:
        super().__init__()
        self.real = real

    async def load_context(self, envelope: Any) -> Context:
        return Context(capabilities=["payment_agent", "subscription_agent"])

    async def signals(self, envelope: Any, context: Context) -> Signals:
        return make_signals(Intent.BILLING_INVOICE)

    async def plan_args(self, envelope: Any, context: Context, signals: Signals) -> Plan:
        return await self.real.plan_args(envelope, context, signals)

    async def next_step(self, state: TurnState) -> list[Task]:
        return list(await self.real.next_step(state))

    def task_risk(self, task: Task) -> RiskClass | None:
        return self.real.task_risk(task)

    async def execute_spoke(self, task: Task, envelope: Any, context: Context) -> AgentResult:
        self.executed_tasks.append(task.agent)
        return await self.real.execute_spoke(task, envelope, context)

    async def compose(self, state: TurnState) -> Draft:
        return await self.real.compose(state)

    async def verify(self, state: TurnState) -> Verification:
        return await self.real.verify(state)

    async def gate(self, state: TurnState) -> Any:
        return await self.real.gate(state)


async def _company(shop: Shop) -> tuple[UUID, str]:
    """Payments switched on with one bank account, and a customer with one invoice."""
    await activate_only(shop.w, shop.tenant, "subscriptions")
    enabled = await shop.w.q(
        "SELECT is_enabled, client_enabled FROM capability_registry"
        " WHERE client_id = %s AND name = 'payment_agent'",
        shop.tenant,
    )
    assert enabled == [{"is_enabled": True, "client_enabled": True}]
    ledger = Ledger(shop.w, shop.tenant, uuid4())
    await ledger.payments_on()
    await shop.w.q(
        "INSERT INTO payment_methods (client_id, kind, label, details) VALUES (%s,%s,%s,%s)",
        shop.tenant,
        "transfer",
        "Bank",
        Jsonb(
            {
                "bank_name": "Bank Sintetik",
                "account_name": "Kedai Contoh Sdn Bhd",
                "account_number": ACCOUNT,
            }
        ),
    )
    contact = await shop.customer()
    invoice_id = await ledger.invoice(contact=contact, amount="1059.90", due_at=DUE)
    rows = await shop.w.q("SELECT reference FROM invoices WHERE id = %s", invoice_id)
    return contact, str(rows[0]["reference"])


async def payment_turn(
    shop: Shop, contact: UUID, payload: dict[str, Any], *, say: str, language: str = "en"
) -> tuple[PaymentTurn, PaymentModel, TurnState]:
    plain = make_envelope(say)
    envelope: Envelope = plain.model_copy(
        update={
            "client_id": shop.client,
            "trace_id": uuid4(),
            "idempotency_key": f"telegram:{uuid4()}",
            "contact": Contact(
                id=contact,
                external_id="synthetic-customer",
                channel=plain.channel,
                display_name=None,
            ),
            "message": plain.message.model_copy(
                update={"body_redacted": redact(say), "language": language}
            ),
        }
    )
    model = PaymentModel(payload)
    made = PaymentTurn(PostgresTurnDependencies(shop.w.pool, llm=build_llm_provider(model)))
    return made, model, await run_turn(envelope, made)  # type: ignore[arg-type]


@requires_docker
async def test_a_whole_payment_turn_sends_the_facts_with_the_exact_account(shop: Shop) -> None:
    contact, ref = await _company(shop)

    made, model, state = await payment_turn(
        shop, contact, {"action": "instructions"}, say="How do I pay my invoice?"
    )

    assert made.executed_tasks == ["payment_agent"] and made.fallbacks == []
    assert "ComposeOut" not in model.asked and "ReviewOut" not in model.asked
    assert state.draft is not None and state.draft.composer == "template"
    assert len(made.emitted) == 1
    body = made.emitted[0].body
    assert body == "\n".join(fact.text for fact in state.facts)
    assert [fact.id.split(":", 1)[1] for fact in state.facts] == [
        "payment_invoice_1",
        "payment_method_1",
        "payment_reference_1",
        "payment_after_1",
    ]
    assert f"account {ACCOUNT}," in body and ref in body and "MYR 1,059.90" in body


@requires_docker
async def test_a_malay_message_gets_the_malay_facts(shop: Shop) -> None:
    contact, ref = await _company(shop)

    made, _, _ = await payment_turn(
        shop,
        contact,
        {"action": "instructions"},
        say="Saya nak bayar macam mana?",
        language="und",  # what Telegram and WhatsApp report
    )

    body = made.emitted[0].body
    assert f"Pindahan bank: Bank Sintetik, akaun {ACCOUNT}, nama Kedai Contoh Sdn Bhd." in body
    assert f"Letak {ref} sebagai rujukan bayaran ya" in body
    assert body.endswith(
        "Lepas bayar, bagitahu bila dan dari bank mana, nanti kami sahkan bila duit dah masuk "
        "akaun."
    )


async def test_the_planner_is_told_of_the_payment_agent_and_its_input() -> None:
    assert deps.CAPABILITIES["payment_agent"] == (
        "How to pay the customer's own invoices (bank account, DuitNow and the invoice "
        "reference), whether a payment was recorded, and noting that the customer says they "
        "paid; the server finds the invoices from this chat"
    )
    assert deps.CAPABILITIES["invoice_agent"] == "Invoice lookup and status"
    assert "payment_agent" in PostgresTurnDependencies.known_agents
    provider = build_llm_provider(PaymentModel({}))
    assert provider._read_actions["payment_agent"] == ["instructions", "status"]
