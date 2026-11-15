"""The reply of a payment turn is the payment agent's facts as they stand, never model text.

An account number, a reference and an amount reach the customer exactly as the database holds
them: the composer is not asked (`composer="template"`), so the coordinator's check is grounding
alone. A handoff still wins, and every other turn is composed as before.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

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
from csa.payments.copy import local_date
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


def _result(
    task_id: str,
    agent: str,
    facts: list[tuple[str, str]],
    *,
    ok: bool = True,
    ms: dict[str, str] | None = None,
) -> Any:
    return AgentResult(
        ok=ok,
        status="ok" if ok else "error",
        task_id=task_id,
        agent=agent,
        data=AgentResultData(
            facts=[
                Fact(id=fact_id, text=text, source=agent, text_ms=(ms or {}).get(fact_id))
                for fact_id, text in facts
            ]
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
    # Reads of other tasks, and a write that failed (whatever facts it carries), stay out.
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
            _result("t1-booking_agent", "booking_agent", [BOOKED], ok=False),
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


async def test_a_facts_reply_never_cuts_a_fact_in_half() -> None:
    """Over Draft's 8000 characters, whole facts are kept and the rest left out: an account
    number is never sent cut short."""
    long_fact = ("write_1", Fact(id="write_1", text="x" * 7950, source="subscription_agent"))
    account = ("pay:payment_method_1", Fact(id="payment_method_1", text=FACTS[1][1], source="p"))
    draft = deps._facts_draft([long_fact, account], "en")
    assert draft.text == "x" * 7950 and ACCOUNT not in draft.text
    assert draft.fact_ids == ["write_1"]
    whole = deps._facts_draft([account, long_fact], "en")
    assert whole.text == FACTS[1][1] and whole.fact_ids == ["pay:payment_method_1"]
    short = deps._facts_draft([account, account], "en")
    assert short.text == f"{FACTS[1][1]}\n{FACTS[1][1]}" and len(short.fact_ids) == 2


def _malay(state: TurnState, language: str = "ms") -> TurnState:
    message = state.envelope.message.model_copy(update={"language": language})
    return state.model_copy(
        update={"envelope": state.envelope.model_copy(update={"message": message})}
    )


def _two_facts() -> list[tuple[str, Fact]]:
    return [
        (
            "t0:a",
            Fact(
                id="a",
                text="Booked: haircut.",
                text_ms="Dah siap tempah: gunting rambut.",
                source="s",
            ),
        ),
        ("t0:b", Fact(id="b", text="Booked: shave.", source="s")),
    ]


async def test_facts_draft_uses_the_malay_text_on_a_malay_turn() -> None:
    draft = deps._facts_draft(_two_facts(), "ms")
    assert draft.text == "Dah siap tempah: gunting rambut.\nBooked: shave."
    assert draft.language == "ms" and draft.composer == "template"
    assert draft.fact_ids == ["t0:a", "t0:b"]


async def test_facts_draft_falls_back_to_english_without_a_malay_text() -> None:
    plain = [("t0:b", Fact(id="b", text="Booked: shave.", source="s"))]
    assert deps._facts_draft(plain, "ms").text == "Booked: shave."


async def test_english_turns_ignore_text_ms() -> None:
    for language in ("en", "zh"):
        assert deps._facts_draft(_two_facts(), language).text == "Booked: haircut.\nBooked: shave."


async def test_a_malay_text_counts_toward_the_draft_limit() -> None:
    long_ms = ("w0", Fact(id="w0", text="short", text_ms="x" * 7950, source="s"))
    account = ("p:m", Fact(id="m", text=FACTS[1][1], source="p"))
    draft = deps._facts_draft([long_ms, account], "ms")
    assert draft.text == "x" * 7950 and draft.fact_ids == ["w0"]
    assert deps._facts_draft([long_ms, account], "en").fact_ids == ["w0", "p:m"]


async def test_text_ms_is_optional_and_never_empty() -> None:
    assert Fact(id="a", text="t", source="s").text_ms is None
    with pytest.raises(ValueError):
        Fact(id="a", text="t", text_ms="", source="s")


async def test_the_composer_input_does_not_carry_text_ms() -> None:
    # The model is shown the facts' English text only: the same prompts whether or not a Malay
    # text is set (so recorded goldens do not move).
    calls: list[list[str]] = []
    for ms in (None, "Dah siap tempah: gunting rambut."):
        model = Model({"reply": "Booked.", "used_fact_ids": ["t0-kb_agent:booked_1"]})
        state = _state(
            [
                _result(
                    "t0-kb_agent",
                    "kb_agent",
                    [("booked_1", "Booked: haircut.")],
                    ms={"booked_1": ms} if ms else None,
                )
            ]
        )
        await _deps(model)._compose(state)
        calls.append(model.calls)
    assert calls[0] == calls[1] and len(calls[0]) == 1
    assert "Dah siap" not in calls[1][0] and "text_ms" not in calls[1][0]


async def test_a_malay_payment_turn_that_wrote_tells_the_malay_write_text_first() -> None:
    model = Model({"reply": "x", "used_fact_ids": []})
    made = _deps(model)
    ms_booked = "Dah siap tempah: gunting rambut, Selasa 15:00."
    state = _malay(
        _state(
            [
                _result("t0-booking_agent", "booking_agent", [BOOKED], ms={BOOKED[0]: ms_booked}),
                _result("t1-payment_agent", "payment_agent", FACTS),
            ],
            actions={"t0-booking_agent": "book", "t1-payment_agent": "instructions"},
        )
    )

    draft = await made.compose(state)

    assert draft == Draft(
        text="\n".join([ms_booked, *(text for _, text in FACTS)]),
        language="ms",
        fact_ids=[f"t0-booking_agent:{BOOKED[0]}", *(f"t1-payment_agent:{i}" for i, _ in FACTS)],
        composer="template",
    )
    assert model.calls == []
    # The same turn in English keeps the English write text.
    english = await made.compose(state.model_copy(update={"envelope": _state([]).envelope}))
    assert english.text.splitlines()[0] == BOOKED[1] and english.language == "en"


async def test_a_first_malay_text_over_the_limit_is_cut_not_dropped() -> None:
    first = ("w0", Fact(id="w0", text="short", text_ms="m" * 9000, source="s"))
    draft = deps._facts_draft([first], "ms")
    assert draft.text == "m" * deps.DRAFT_MAX and draft.fact_ids == ["w0"]
    assert deps._facts_draft([first], "en").text == "short"


def _telegram(state: TurnState, body: str) -> TurnState:
    """The turn as Telegram and WhatsApp deliver it: language `und`, the words decide."""
    message = state.envelope.message.model_copy(
        update={"language": "und", "body_raw": body, "body_redacted": body}
    )
    return state.model_copy(
        update={"envelope": state.envelope.model_copy(update={"message": message})}
    )


@pytest.mark.parametrize(
    ("body", "language", "first_line"),
    [
        ("Saya nak tempah esok boleh tak", "ms", "Dah siap tempah: gunting rambut."),
        ("Please book me a haircut", "en", BOOKED[1]),
    ],
)
async def test_the_booking_fallback_picks_the_text_by_the_resolved_language(
    body: str, language: str, first_line: str
) -> None:
    # Composing fails (here: raises), so the booking facts go out as they stand; the channel says
    # `und`, so the turn's words decide the language of the text and of the draft.
    made = _deps(Model(None))

    async def broken(_state: TurnState) -> Draft:
        raise RuntimeError("composer down")

    made._compose = broken  # type: ignore[method-assign]
    state = _telegram(
        _state(
            [
                _result(
                    "t0-booking_agent",
                    "booking_agent",
                    [BOOKED],
                    ms={BOOKED[0]: "Dah siap tempah: gunting rambut."},
                )
            ],
            actions={"t0-booking_agent": "book"},
        ),
        body,
    )
    draft = await made.compose(state)
    assert draft.composer == "template" and draft.text == first_line
    assert draft.language == language


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


class TicketAndPaymentModel(PaymentModel):
    """Plans a ticket and the payment answer in one turn ("my card was refused, open a ticket;
    how do I pay?"), and refuses to write or review a reply."""

    async def json_completion(self, schema: Any, *, system: str, user: str, **_: Any) -> Any:
        if schema is PlanOut:
            self.asked.append(schema.__name__)
            ticket = {"action": "create", "subject": "Kad ditolak", "category": "billing"}
            return PlanOut(
                tasks=[
                    PlannedTask(agent="ticket_agent", input=ticket),
                    PlannedTask(agent="payment_agent", input={"action": "instructions"}),
                ],
                reply_strategy="auto_reply",
            )
        return await super().json_completion(schema, system=system, user=user)


class TicketAndPaymentTurn(PaymentTurn):
    known_agents = frozenset({"payment_agent", "ticket_agent", "escalation_agent"})


@requires_docker
@pytest.mark.parametrize(
    ("say", "language"),
    [
        ("Kad saya kena tolak, tolong buka tiket. Nak bayar macam mana?", "ms"),
        ("My card was refused, please open a ticket. How do I pay?", "en"),
    ],
)
async def test_a_ticket_and_a_payment_answer_go_out_in_the_turn_language(
    shop: Shop, say: str, language: str
) -> None:
    # The real planner seam, ticket agent, payment agent and facts-only reply: on a Malay turn
    # (the channel says "und", the words decide) the ticket line is the agent's Malay text and the
    # payment lines are the Malay copy, so no English sentence reaches the customer.
    from tests.test_booking_agent import conversation

    contact, ref = await _company(shop)
    chat = await conversation(shop, contact)
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
            "conversation": plain.conversation.model_copy(update={"id": chat}),
            "message": plain.message.model_copy(
                update={"body_raw": say, "body_redacted": redact(say), "language": "und"}
            ),
        }
    )
    model = TicketAndPaymentModel({})
    made = TicketAndPaymentTurn(
        PostgresTurnDependencies(shop.w.pool, llm=build_llm_provider(model))
    )

    state = await run_turn(envelope, made)  # type: ignore[arg-type]

    assert made.executed_tasks == ["ticket_agent", "payment_agent"] and made.fallbacks == []
    assert "ComposeOut" not in model.asked and "ReviewOut" not in model.asked
    assert state.draft is not None and state.draft.composer == "template"
    assert state.draft.language == language and len(made.emitted) == 1
    rows = await shop.w.q(
        "SELECT t.id, t.sla_due_at, c.timezone FROM tickets t JOIN clients c ON c.id = t.client_id"
        " WHERE t.client_id = %s",
        shop.tenant,
    )
    assert len(rows) == 1
    n, due, zone = rows[0]["id"], rows[0]["sla_due_at"], rows[0]["timezone"]
    english = (
        f"Ticket #{n} created (billing, normal priority), target response by "
        f"{due.astimezone(ZoneInfo(zone)):%d %b %Y}."
    )
    ticket = next(fact for fact in state.facts if fact.source == "ticket_agent")
    assert ticket.text == english  # what the composer and grounding see, either way
    lines = made.emitted[0].body.splitlines()
    if language == "en":
        assert lines[0] == english
        assert f"Bank transfer: Bank Sintetik, account {ACCOUNT}" in lines[2]
        assert not any("Tiket" in line or "Pindahan" in line for line in lines)
        return
    assert lines == [
        f"Tiket #{n} dah dibuat (bil, keutamaan biasa), sasaran balasan paling lewat "
        f"{local_date(due, 'ms', zone)}.",
        f"Invois {ref}: MYR 1,059.90 untuk Basic, kena bayar sebelum "
        f"{local_date(DUE, 'ms', zone)}.",
        f"Pindahan bank: Bank Sintetik, akaun {ACCOUNT}, nama Kedai Contoh Sdn Bhd.",
        f"Letak {ref} sebagai rujukan bayaran ya, senang kami padankan.",
        "Lepas bayar, bagitahu bila dan dari bank mana, nanti kami sahkan bila duit dah masuk "
        "akaun.",
    ]
    body = made.emitted[0].body
    for word in ("Ticket", "created", "priority", "Invoice", "Bank transfer", "Please", "After"):
        assert word not in body
    for word in (" anda ", "Anda ", "sila", "Sila", "pihak kami"):
        assert word not in body


class OrderAndPaymentModel(PaymentModel):
    """Plans an order draft and the payment answer in one turn ("I want two of these; how do I
    pay?"), and refuses to write or review a reply."""

    async def json_completion(self, schema: Any, *, system: str, user: str, **_: Any) -> Any:
        if schema is PlanOut:
            self.asked.append(schema.__name__)
            order = {"action": "create_draft", "items": [{"sku": "SYN-1", "qty": 2}]}
            return PlanOut(
                tasks=[
                    PlannedTask(agent="order_agent", input=order),
                    PlannedTask(agent="payment_agent", input={"action": "instructions"}),
                ],
                reply_strategy="auto_reply",
            )
        return await super().json_completion(schema, system=system, user=user)


class OrderAndPaymentTurn(PaymentTurn):
    known_agents = frozenset({"payment_agent", "order_agent", "escalation_agent"})


@requires_docker
@pytest.mark.parametrize(
    ("say", "language"),
    [
        ("Saya nak order dua botol sabun. Nak bayar macam mana?", "ms"),
        ("I want to order two bottles of soap. How do I pay?", "en"),
    ],
)
async def test_an_order_draft_and_a_payment_answer_go_out_in_the_turn_language(
    shop: Shop, say: str, language: str
) -> None:
    # The real planner seam, order agent (Telegram), payment agent and facts-only reply: on a
    # Malay turn (the channel says "und", the words decide) the draft line is the agent's Malay
    # text and the payment lines are the Malay copy, so no English sentence reaches the customer.
    from tests.test_booking_agent import conversation

    contact, ref = await _company(shop)
    await activate_only(shop.w, shop.tenant, "orders_products")
    chat = await conversation(shop, contact)
    await shop.w.q(
        "INSERT INTO products (client_id, sku, name, price, stock_qty) VALUES (%s,%s,%s,%s,%s)",
        shop.tenant,
        "SYN-1",
        "Sabun Sintetik",
        "12.50",
        10,
    )
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
            "conversation": plain.conversation.model_copy(update={"id": chat}),
            "message": plain.message.model_copy(
                update={"body_raw": say, "body_redacted": redact(say), "language": "und"}
            ),
        }
    )
    model = OrderAndPaymentModel({})
    made = OrderAndPaymentTurn(PostgresTurnDependencies(shop.w.pool, llm=build_llm_provider(model)))

    state = await run_turn(envelope, made)  # type: ignore[arg-type]

    assert made.executed_tasks == ["order_agent", "payment_agent"] and made.fallbacks == []
    assert "ComposeOut" not in model.asked and "ReviewOut" not in model.asked
    assert state.draft is not None and state.draft.composer == "template"
    assert state.draft.language == language and len(made.emitted) == 1
    rows = await shop.w.q("SELECT id FROM orders WHERE client_id = %s", shop.tenant)
    assert len(rows) == 1
    n = rows[0]["id"]
    english = (
        f"Draft order {n}: 2 x Sabun Sintetik, total MYR 25.00. "
        "It is not placed until you confirm it."
    )
    order = next(fact for fact in state.facts if fact.source == "order_agent")
    assert order.text == english  # what the composer and grounding see, either way
    lines = made.emitted[0].body.splitlines()
    if language == "en":
        assert lines[0] == english
        assert f"Bank transfer: Bank Sintetik, account {ACCOUNT}" in lines[2]
        assert not any("Draf pesanan" in line or "Pindahan" in line for line in lines)
        return
    zone = (await shop.w.q("SELECT timezone FROM clients WHERE id = %s", shop.tenant))[0]
    assert lines == [
        f"Draf pesanan {n}: 2 x Sabun Sintetik, jumlah MYR 25.00. "
        "Pesanan ni belum dibuat lagi selagi awak tak sahkan.",
        f"Invois {ref}: MYR 1,059.90 untuk Basic, kena bayar sebelum "
        f"{local_date(DUE, 'ms', zone['timezone'])}.",
        f"Pindahan bank: Bank Sintetik, akaun {ACCOUNT}, nama Kedai Contoh Sdn Bhd.",
        f"Letak {ref} sebagai rujukan bayaran ya, senang kami padankan.",
        "Lepas bayar, bagitahu bila dan dari bank mana, nanti kami sahkan bila duit dah masuk "
        "akaun.",
    ]
    body = made.emitted[0].body
    for word in ("Draft", "placed", "confirm", "total", "Invoice", "Bank transfer", "After"):
        assert word not in body
    for word in (" anda ", "Anda ", "sila", "Sila", "pihak kami"):
        assert word not in body


class BookingAndPaymentModel(PaymentModel):
    """Plans a booking and the payment answer in one turn ("book me a haircut on Tuesday at ten;
    how do I pay?"), and refuses to write or review a reply."""

    def __init__(self, booking: dict[str, Any]) -> None:
        super().__init__({})
        self.booking = booking

    async def json_completion(self, schema: Any, *, system: str, user: str, **_: Any) -> Any:
        if schema is PlanOut:
            self.asked.append(schema.__name__)
            return PlanOut(
                tasks=[
                    PlannedTask(agent="booking_agent", input=self.booking),
                    PlannedTask(agent="payment_agent", input={"action": "instructions"}),
                ],
                reply_strategy="auto_reply",
            )
        return await super().json_completion(schema, system=system, user=user)


class BookingAndPaymentTurn(PaymentTurn):
    known_agents = frozenset({"payment_agent", "booking_agent", "escalation_agent"})


@requires_docker
@pytest.mark.parametrize(
    ("say", "language"),
    [
        ("Saya nak tempah gunting rambut hari Selasa pukul 10. Nak bayar macam mana?", "ms"),
        ("Book me a haircut on Tuesday at 10. How do I pay?", "en"),
    ],
)
async def test_a_booking_and_a_payment_answer_go_out_in_the_turn_language(
    shop: Shop, say: str, language: str
) -> None:
    # The real planner seam, booking agent (Telegram), payment agent and facts-only reply: on a
    # Malay turn (the channel says "und", the words decide) the booking line is the agent's Malay
    # text, with the Malay day and month names and the same numbers, and the payment lines are the
    # Malay copy, so no English sentence reaches the customer. (The golden runner cannot seed
    # bookings, so this whole turn is the booking's mixed-turn cover.)
    from csa.bookings.bot import format_slot, format_slot_ms

    from tests.test_booking_agent import conversation
    from tests.test_bookings_api import soon_tuesday
    from tests.test_bookings_bot import a_type

    contact, ref = await _company(shop)
    kind = await a_type(shop)
    chat = await conversation(shop, contact)
    slot = f"{soon_tuesday()}T10:00"
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
            "conversation": plain.conversation.model_copy(update={"id": chat}),
            "message": plain.message.model_copy(
                update={"body_raw": say, "body_redacted": redact(say), "language": "und"}
            ),
        }
    )
    model = BookingAndPaymentModel({"action": "book", "type_id": kind["id"], "starts_at": slot})
    made = BookingAndPaymentTurn(
        PostgresTurnDependencies(shop.w.pool, llm=build_llm_provider(model))
    )

    state = await run_turn(envelope, made)  # type: ignore[arg-type]

    assert made.executed_tasks == ["booking_agent", "payment_agent"] and made.fallbacks == []
    assert "ComposeOut" not in model.asked and "ReviewOut" not in model.asked
    assert state.draft is not None and state.draft.composer == "template"
    assert state.draft.language == language and len(made.emitted) == 1
    assert [r["status"] for r in await shop.rows()] == ["confirmed"]
    at = datetime.fromisoformat(slot).replace(tzinfo=UTC)  # the shop's zone is UTC
    when = format_slot(at, ZoneInfo("UTC"))
    english = f"Booking confirmed: Haircut on {when}, with Person 0, by phone."
    booking = next(fact for fact in state.facts if fact.source == "booking_agent")
    assert booking.text == english  # what the composer and grounding see, either way
    lines = made.emitted[0].body.splitlines()
    if language == "en":
        assert lines[0] == english
        assert f"Bank transfer: Bank Sintetik, account {ACCOUNT}" in lines[2]
        assert not any("Tempahan" in line or "Pindahan" in line for line in lines)
        return
    malay_slot = format_slot_ms(at, ZoneInfo("UTC"))
    assert malay_slot.startswith("Selasa ") and malay_slot.endswith(
        f"10:00 pagi ({slot.replace('T', ' ')})"
    )
    assert lines == [
        f"Tempahan dah disahkan: Haircut pada {malay_slot}, dengan Person 0, melalui telefon.",
        f"Invois {ref}: MYR 1,059.90 untuk Basic, kena bayar sebelum "
        f"{local_date(DUE, 'ms', 'UTC')}.",
        f"Pindahan bank: Bank Sintetik, akaun {ACCOUNT}, nama Kedai Contoh Sdn Bhd.",
        f"Letak {ref} sebagai rujukan bayaran ya, senang kami padankan.",
        "Lepas bayar, bagitahu bila dan dari bank mana, nanti kami sahkan bila duit dah masuk "
        "akaun.",
    ]
    body = made.emitted[0].body
    for word in ("Booking", "confirmed", "Tuesday", "with", "by phone", "Invoice", "After"):
        assert word not in body
    for word in (" anda ", "Anda ", "sila", "Sila", "pihak kami"):
        assert word not in body


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


@requires_docker
async def test_the_payment_agent_is_offered_only_while_payments_are_on(shop: Shop) -> None:
    """With payments off the classifier and planner never hear of the payment agent (or its
    rules: the dispute escalation among them); switched on, it is offered on the next turn."""
    from tests.test_booking_agent import conversation, envelope_for
    from tests.test_llm_provider import _prompts_for

    await activate_only(shop.w, shop.tenant, "subscriptions")
    contact = await shop.customer()
    envelope = envelope_for(shop, contact, await conversation(shop, contact))
    real = PostgresTurnDependencies(shop.w.pool)

    off = await real.load_context(envelope)
    assert "payment_agent" not in off.capabilities and "subscription_agent" in off.capabilities
    for call in ("classify", "plan"):
        system, _ = await _prompts_for(off, call)
        assert "payment_agent" not in system and "Plan payment tasks alone" not in system

    ledger = Ledger(shop.w, shop.tenant, uuid4())
    await ledger.payments_on()
    on = await real.load_context(envelope)  # the same deps: the switch is never cached
    assert "payment_agent" in on.capabilities
    system, _ = await _prompts_for(on, "plan")
    assert "payment_agent" in system and "Plan payment tasks alone" in system

    await ledger.payments_on(enabled=False)
    assert "payment_agent" not in (await real.load_context(envelope)).capabilities
