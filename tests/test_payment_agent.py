"""The customer bot's payment agent: how to pay, whether a payment was recorded, "I've paid".

Every fact is the customer's own invoices only (`ctx.contact_id`), worded from `csa.payments.copy`
in English or Malay, with the reference, amount and account number inserted from the database. A
`paid` answer needs the ledger's payments row. A typed reference narrows within the customer's own
invoices; another customer's reference is "can't find" and nothing of that invoice is shown or
changed. Staff-only fields (void reason, voider, payment note, who recorded it) never reach a fact.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from csa.agents.base import AgentContext
from csa.agents.payments import PaymentAgent, PaymentInput
from csa.contracts.callbacks import PendingActionStore
from csa.contracts.facts import AgentResult
from csa.payments.copy import local_date
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld
from tests.test_payment_invoices import Ledger, _ledger

pytest_plugins = ["tests.test_console_sessions"]

SENTINEL = "STAFF-ONLY-SENTINEL"
TZ = "Asia/Kuala_Lumpur"
# 12:00 in Kuala Lumpur on 20 March 2031, so the local due date is the same day in every zone test.
DUE = datetime(2031, 3, 20, 4, 0, tzinfo=UTC)
ACCOUNT = "512000000017"
TRANSFER = {
    "bank_name": "Bank Sintetik",
    "account_name": "Kedai Contoh Sdn Bhd",
    "account_number": ACCOUNT,
}
QR = {"duitnow_id": "512000000018", "account_name": "Kedai Contoh Sdn Bhd"}

AFTER_EN = (
    "After paying, tell me when and from which bank, and we'll confirm once it shows in our "
    "account."
)
AFTER_MS = (
    "Lepas bayar, bagitahu bila dan dari bank mana, nanti kami sahkan bila duit dah masuk akaun."
)
NOTHING_EN = "You have no invoice waiting for payment."
NOTHING_MS = "Takde invois yang perlu dibayar sekarang."
NOT_SET_UP_EN = (
    "Payment details aren't set up in this chat yet. I can pass your question to the team."
)
NOT_SET_UP_MS = (
    "Butiran bayaran belum disediakan kat sini lagi. Saya boleh hantar soalan ni kepada team."
)


def claimed_en(ref: str, amount: str) -> str:
    return (
        f"Invoice {ref} ({amount}): you told us you paid. It isn't confirmed yet; we confirm it "
        "once it shows in our account."
    )


# --- the input the planner fills ------------------------------------------------------------------


def test_the_input_refuses_extra_fields_and_any_amount() -> None:
    for payload in (
        {"action": "instructions", "amount": "29.90"},
        {"action": "instructions", "account_number": ACCOUNT},
        {"action": "status", "invoice_id": "inv_x"},
        {"action": "pay"},
        {"action": "claim_paid", "reference": "A" * 13},
        {"action": "claim_paid", "note": "x" * 301},
    ):
        with pytest.raises(ValidationError):
            PaymentInput.model_validate(payload)
    schema = PaymentInput.model_json_schema()
    assert set(schema["properties"]) == {"action", "reference", "note"}
    assert schema["properties"]["action"]["enum"] == ["instructions", "status", "claim_paid"]
    assert all(p.get("description") for p in schema["properties"].values())
    text = json.dumps(schema).lower()
    assert "from this chat" in text and "never" in text and "amount" in text
    # A model fills an unused field with a blank as often as it leaves it out.
    blank = PaymentInput.model_validate({"action": "status", "reference": " ", "note": ""})
    assert blank.reference is None and blank.note is None
    # A reference with nothing printable in it is no reference: it would echo as nothing.
    for junk in ("\x07", "\x07 \x1b", "\u200b"):
        assert (
            PaymentInput.model_validate({"action": "status", "reference": junk}).reference is None
        )
    kept = PaymentInput.model_validate({"action": "status", "reference": "\x07a"})
    assert kept.reference == "\x07a"


def test_reads_are_read_and_a_claim_is_a_draft() -> None:
    agent = PaymentAgent()
    assert agent.name == "payment_agent" and agent.side_effects == "reversible"
    assert agent.ACTION_RISK == {"instructions": "read", "status": "read", "claim_paid": "draft"}
    assert agent.risk(PaymentInput(action="claim_paid")) == "draft"
    assert agent.Input is PaymentInput


# --- against the database -----------------------------------------------------------------------


class Shop:
    """One company with the payment agent switched on and payments set up."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.w = ledger.w
        self.client = ledger.tenant

    async def switch(self, on: bool) -> None:
        rows = await self.w.q(
            "UPDATE capability_registry SET is_enabled = %s, client_enabled = %s"
            " WHERE client_id = %s AND name = 'payment_agent' RETURNING name",
            on,
            on,
            self.client,
        )
        assert len(rows) == 1, "onboarding provisions payment_agent"

    async def method(
        self, kind: str, details: dict[str, Any], *, order: int = 0, active: bool = True
    ) -> None:
        await self.w.q(
            "INSERT INTO payment_methods (client_id, kind, label, details, is_active, sort_order)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            self.client,
            kind,
            f"{kind.title()} method",
            Jsonb(details),
            active,
            order,
        )

    async def invoice(self, contact: UUID, **kw: Any) -> tuple[str, str]:
        kw.setdefault("due_at", DUE)
        invoice_id = await self.ledger.invoice(contact=contact, **kw)
        return invoice_id, await self.reference(invoice_id)

    async def reference(self, invoice_id: str) -> str:
        rows = await self.w.q("SELECT reference FROM invoices WHERE id = %s", invoice_id)
        return str(rows[0]["reference"])

    async def row(self, invoice_id: str) -> dict[str, Any]:
        return (await self.w.q("SELECT * FROM invoices WHERE id = %s", invoice_id))[0]

    async def run(
        self, contact: UUID, payload: dict[str, Any], *, locale: str = "en"
    ) -> AgentResult:
        ctx = AgentContext(
            client_id=self.client,
            contact_id=contact,
            trace_id=uuid.uuid4(),
            conversation_id=None,
            idempotency_key=f"synthetic:{uuid.uuid4()}",
            channel="telegram",
            pool=self.w.pool,
            pending_actions=PendingActionStore(self.w.pool),
            timezone=TZ,
            locale=locale,
        )
        return await PaymentAgent().run(ctx, PaymentInput.model_validate(payload))


def facts(result: AgentResult) -> list[tuple[str, str]]:
    assert result.ok, result.error
    assert all(f.source == "payment_agent" for f in result.facts)
    return [(f.id, f.text) for f in result.facts]


@pytest.fixture
async def shop(sessions: SessionWorld) -> Shop:
    made = Shop(await _ledger(sessions, sessions.tenants[0]))
    await made.switch(True)
    await made.ledger.payments_on()
    await made.method("transfer", TRANSFER, order=1)
    await made.method("cash", {}, order=0)  # never listed: Payments 2 pays cash through a booking
    return made


@requires_docker
@pytest.mark.asyncio
async def test_instructions_give_the_exact_invoice_account_and_reference(shop: Shop) -> None:
    contact = await shop.ledger.contact()
    _, ref = await shop.invoice(contact, amount="1059.90")

    english = facts(await shop.run(contact, {"action": "instructions"}))
    assert english == [
        ("payment_invoice_1", f"Invoice {ref}: MYR 1,059.90 for Basic, due 20 March 2031."),
        (
            "payment_method_1",
            f"Bank transfer: Bank Sintetik, account {ACCOUNT}, name Kedai Contoh Sdn Bhd.",
        ),
        (
            "payment_reference_1",
            f"Please put {ref} as the payment reference so we can match your payment.",
        ),
        ("payment_after_1", AFTER_EN),
    ]

    malay = facts(await shop.run(contact, {"action": "instructions"}, locale="ms"))
    assert malay == [
        (
            "payment_invoice_1",
            f"Invois {ref}: MYR 1,059.90 untuk Basic, kena bayar sebelum 20 Mac 2031.",
        ),
        (
            "payment_method_1",
            f"Pindahan bank: Bank Sintetik, akaun {ACCOUNT}, nama Kedai Contoh Sdn Bhd.",
        ),
        ("payment_reference_1", f"Letak {ref} sebagai rujukan bayaran ya, senang kami padankan."),
        ("payment_after_1", AFTER_MS),
    ]


@requires_docker
@pytest.mark.asyncio
async def test_two_invoices_and_two_methods_list_each_and_ask_for_each_reference(
    shop: Shop,
) -> None:
    await shop.method("qr", QR, order=2)
    await shop.method("transfer", {**TRANSFER, "account_number": "512000000019"}, active=False)
    contact = await shop.ledger.contact()
    _, first = await shop.invoice(contact, amount="29.90")
    _, second = await shop.invoice(contact, amount="59.90", due_at=DUE + timedelta(days=30))

    got = facts(await shop.run(contact, {"action": "instructions"}))
    assert [fact_id for fact_id, _ in got] == [
        "payment_invoice_1",
        "payment_invoice_2",
        "payment_method_1",
        "payment_method_2",
        "payment_reference_1",
        "payment_after_1",
    ]
    texts = dict(got)
    assert first in texts["payment_invoice_1"] and second in texts["payment_invoice_2"]
    assert texts["payment_method_2"] == "DuitNow: 512000000018, name Kedai Contoh Sdn Bhd."
    assert texts["payment_reference_1"] == (
        "Put each invoice's reference with its payment so we can match it."
    )
    joined = "\n".join(texts.values())
    assert "Cash" not in joined and "512000000019" not in joined


@requires_docker
@pytest.mark.asyncio
async def test_a_claimed_invoice_is_not_asked_for_again(shop: Shop) -> None:
    contact = await shop.ledger.contact()
    _, ref = await shop.invoice(contact, status="proof_submitted", amount="29.90")

    got = facts(await shop.run(contact, {"action": "instructions"}))
    assert got == [("payment_invoice_1", claimed_en(ref, "MYR 29.90"))]


@requires_docker
@pytest.mark.asyncio
async def test_no_open_invoice_payments_off_and_switched_off(shop: Shop) -> None:
    contact = await shop.ledger.contact()
    await shop.invoice(contact, status="void")
    await shop.invoice(contact, status="draft")
    assert facts(await shop.run(contact, {"action": "instructions"})) == [
        ("payment_nothing_due_1", NOTHING_EN)
    ]
    assert facts(await shop.run(contact, {"action": "status"}, locale="ms")) == [
        ("payment_nothing_due_1", NOTHING_MS)
    ]

    await shop.invoice(contact)
    await shop.ledger.payments_on(enabled=False)
    for action in ("instructions", "status", "claim_paid"):
        assert facts(await shop.run(contact, {"action": action})) == [
            ("payment_not_set_up_1", NOT_SET_UP_EN)
        ]
    assert facts(await shop.run(contact, {"action": "status"}, locale="ms-MY")) == [
        ("payment_not_set_up_1", NOT_SET_UP_MS)
    ]

    await shop.ledger.payments_on()
    await shop.switch(False)
    refused = await shop.run(contact, {"action": "instructions"})
    assert refused.ok is False and refused.error is not None
    assert refused.error.code == "disabled" and refused.facts == []


@requires_docker
@pytest.mark.asyncio
async def test_status_says_unpaid_claimed_or_paid_only_with_a_payment(shop: Shop) -> None:
    contact = await shop.ledger.contact()
    _, unpaid = await shop.invoice(contact, amount="29.90")
    _, claimed = await shop.invoice(
        contact, status="proof_submitted", amount="39.90", due_at=DUE + timedelta(days=1)
    )
    paid_at = datetime.now(UTC) - timedelta(days=2)
    paid_id, paid = await shop.invoice(contact, status="paid", amount="49.90", paid_at=paid_at)
    await shop.ledger.ledger_payment(paid_id, received_at=paid_at)
    # An old seed: `paid` with no payments row is never presented, as paid or otherwise.
    _, seeded = await shop.invoice(
        contact, status="paid", amount="59.90", paid_at=datetime.now(UTC) - timedelta(days=1)
    )

    got = facts(await shop.run(contact, {"action": "status"}))
    assert got == [
        (
            "payment_status_1",
            f"Invoice {unpaid} (MYR 29.90) is not paid yet. No payment has been recorded for it.",
        ),
        ("payment_status_2", claimed_en(claimed, "MYR 39.90")),
        (
            "payment_status_3",
            f"Invoice {paid} (MYR 49.90) was paid on {local_date(paid_at, 'en', TZ)}.",
        ),
    ]
    assert seeded not in json.dumps(got)

    malay = dict(facts(await shop.run(contact, {"action": "status"}, locale="ms")))
    assert malay["payment_status_1"] == (
        f"Invois {unpaid} (MYR 29.90) belum dibayar. Belum ada bayaran direkodkan untuknya."
    )
    assert malay["payment_status_2"] == (
        f"Invois {claimed} (MYR 39.90): awak dah bagitahu dah bayar. Belum disahkan lagi; kami "
        "sahkan bila duit dah masuk akaun."
    )
    assert malay["payment_status_3"] == (
        f"Invois {paid} (MYR 49.90) dah dibayar pada {local_date(paid_at, 'ms', TZ)}."
    )


@requires_docker
@pytest.mark.asyncio
async def test_a_claim_waits_for_staff_and_is_noted_once(shop: Shop) -> None:
    contact = await shop.ledger.contact()
    invoice_id, ref = await shop.invoice(contact, amount="29.90")
    note = "dah transfer pagi tadi dari Maybank"

    noted = facts(await shop.run(contact, {"action": "claim_paid", "note": note}))
    assert noted == [
        (
            "payment_claim_noted_1",
            f"Thanks, I've noted that you paid invoice {ref}. We'll confirm once it shows in our "
            "account; until then it isn't marked as paid.",
        )
    ]
    row = await shop.row(invoice_id)
    assert row["status"] == "proof_submitted" and row["claim_note"] == note
    assert row["claimed_at"] is not None and await shop.ledger.payments(invoice_id) == []

    again = facts(await shop.run(contact, {"action": "claim_paid", "reference": ref.lower()}))
    assert again == [("payment_claim_already_1", claimed_en(ref, "MYR 29.90"))]
    assert (await shop.row(invoice_id))["claim_note"] == note


@requires_docker
@pytest.mark.asyncio
async def test_a_claim_in_malay_and_on_a_paid_invoice(shop: Shop) -> None:
    contact = await shop.ledger.contact()
    _, ref = await shop.invoice(contact)
    assert facts(await shop.run(contact, {"action": "claim_paid"}, locale="ms")) == [
        (
            "payment_claim_noted_1",
            f"Terima kasih, saya dah catat awak bayar invois {ref}. Kami sahkan bila duit dah "
            "masuk akaun; sebelum tu ia belum ditanda dibayar.",
        )
    ]
    paid_at = datetime.now(UTC) - timedelta(days=3)
    paid_id, paid = await shop.invoice(contact, status="paid", paid_at=paid_at)
    await shop.ledger.ledger_payment(paid_id, received_at=paid_at)
    assert facts(await shop.run(contact, {"action": "claim_paid", "reference": paid})) == [
        ("payment_already_paid_1", f"Invoice {paid} is already paid.")
    ]
    assert facts(
        await shop.run(contact, {"action": "claim_paid", "reference": paid}, locale="ms")
    ) == [("payment_already_paid_1", f"Invois {paid} dah dibayar.")]


@requires_docker
@pytest.mark.asyncio
async def test_several_open_invoices_ask_which_and_write_nothing(shop: Shop) -> None:
    contact = await shop.ledger.contact()
    first_id, first = await shop.invoice(contact)
    second_id, second = await shop.invoice(contact, status="overdue", due_at=DUE + timedelta(1))

    got = facts(await shop.run(contact, {"action": "claim_paid", "note": "paid today"}))
    assert got == [
        (
            "payment_which_1",
            f"You have more than one invoice waiting: {first}, {second}. Which one did you pay?",
        )
    ]
    malay = facts(await shop.run(contact, {"action": "claim_paid"}, locale="ms"))
    assert malay == [
        (
            "payment_which_1",
            f"Ada lebih dari satu invois menunggu: {first}, {second}. Yang mana satu awak bayar?",
        )
    ]
    for invoice_id, status in ((first_id, "issued"), (second_id, "overdue")):
        row = await shop.row(invoice_id)
        assert row["status"] == status and row["claim_note"] is None

    # Naming one of them settles it.
    named = facts(await shop.run(contact, {"action": "claim_paid", "reference": second}))
    assert named[0][0] == "payment_claim_noted_1" and second in named[0][1]
    assert (await shop.row(second_id))["status"] == "proof_submitted"
    assert (await shop.row(first_id))["status"] == "issued"


@requires_docker
@pytest.mark.asyncio
async def test_another_customers_reference_is_not_found_and_not_touched(shop: Shop) -> None:
    mine = await shop.ledger.contact()
    other = await shop.ledger.contact("Other Customer")
    await shop.invoice(mine, amount="29.90")
    other_id, other_ref = await shop.invoice(other, amount="777.70")

    for action in ("claim_paid", "status", "instructions"):
        got = facts(await shop.run(mine, {"action": action, "reference": other_ref}))
        assert got == [
            ("payment_reference_unknown_1", f"I can't find invoice {other_ref} on this chat.")
        ]
        assert "777" not in json.dumps(got)
    row = await shop.row(other_id)
    assert row["status"] == "issued" and row["claim_note"] is None and row["claimed_at"] is None

    malay = facts(await shop.run(mine, {"action": "status", "reference": other_ref}, locale="ms"))
    assert malay == [
        ("payment_reference_unknown_1", f"Saya tak jumpa invois {other_ref} dalam chat ni.")
    ]
    # Not a reference at all: the customer's own text, cut to 12 characters, is echoed.
    typo = facts(await shop.run(mine, {"action": "status", "reference": "hello there!"}))
    assert typo == [
        ("payment_reference_unknown_1", "I can't find invoice hello there! on this chat.")
    ]
    # Whitespace runs collapse and control characters go before the cut: one line, always.
    odd = facts(await shop.run(mine, {"action": "status", "reference": "ab\n\x07c\t\x1bd  e"}))
    assert odd == [("payment_reference_unknown_1", "I can't find invoice ab c d e on this chat.")]
    # Control characters go first, then the spaces around them collapse.
    spaced = facts(await shop.run(mine, {"action": "status", "reference": "a \x07 b"}))
    assert spaced == [("payment_reference_unknown_1", "I can't find invoice a b on this chat.")]
    # Nothing printable at all: answered as if no reference was given, never "invoice  ".
    bare = facts(await shop.run(mine, {"action": "status"}))
    junk = facts(await shop.run(mine, {"action": "status", "reference": "\x07\x1b"}))
    assert junk == bare and bare[0][0] != "payment_reference_unknown_1"


@requires_docker
@pytest.mark.asyncio
async def test_no_fact_carries_a_staff_only_field(shop: Shop) -> None:
    contact = await shop.ledger.contact()
    staff = str(shop.ledger.staff_id)
    void_id, void_ref = await shop.invoice(contact)
    await shop.w.q(
        "UPDATE invoices SET status = 'void', void_reason = %s, voided_by = %s,"
        " voided_at = now() WHERE id = %s",
        SENTINEL,
        shop.ledger.staff_id,
        void_id,
    )
    paid_at = datetime.now(UTC) - timedelta(days=1)
    paid_id, _ = await shop.invoice(contact, status="paid", paid_at=paid_at)
    await shop.w.q(
        "INSERT INTO payments (client_id, invoice_id, amount, currency, received_at, source,"
        " matched_by, idempotency_key, note, created_by) SELECT client_id, id, amount, currency,"
        " %s, 'staff', 'staff', %s, %s, %s FROM invoices WHERE id = %s",
        paid_at,
        f"seed:{uuid.uuid4()}",
        SENTINEL,
        shop.ledger.staff_id,
        paid_id,
    )
    await shop.invoice(contact, amount="29.90")

    seen: list[str] = []
    for payload in (
        {"action": "instructions"},
        {"action": "status"},
        {"action": "status", "reference": void_ref},
        {"action": "claim_paid", "note": "dari Maybank"},
        {"action": "claim_paid"},
    ):
        for locale in ("en", "ms"):
            result = await shop.run(contact, payload, locale=locale)
            seen.append(result.model_dump_json())
    everything = "\n".join(seen)
    assert SENTINEL not in everything and staff not in everything
    # The void invoice is not one the customer may hear about.
    assert void_ref in everything  # only as the reference they typed, answered "can't find"
    assert everything.count(f"invoice {void_ref} on this chat") == 1


@requires_docker
@pytest.mark.asyncio
async def test_payments_on_without_a_transfer_or_qr_method_is_not_set_up(shop: Shop) -> None:
    contact = await shop.ledger.contact()
    await shop.invoice(contact)
    await shop.method("qr", QR, active=False)
    await shop.w.q(
        "UPDATE payment_methods SET is_active = false WHERE client_id = %s AND kind <> 'cash'",
        shop.client,
    )
    # Only cash (never listed) is left: the customer is never told to pay nowhere.
    assert facts(await shop.run(contact, {"action": "instructions"})) == [
        ("payment_not_set_up_1", NOT_SET_UP_EN)
    ]


@requires_docker
@pytest.mark.asyncio
async def test_a_claim_on_an_invoice_waiting_for_cash_says_it_is_not_paid(shop: Shop) -> None:
    contact = await shop.ledger.contact()
    invoice_id, ref = await shop.invoice(contact, status="cash_pending", amount="29.90")

    got = facts(await shop.run(contact, {"action": "claim_paid", "reference": ref}))

    assert got == [
        (
            "payment_status_1",
            f"Invoice {ref} (MYR 29.90) is not paid yet. No payment has been recorded for it.",
        )
    ]
    row = await shop.row(invoice_id)
    assert row["status"] == "cash_pending" and row["claim_note"] is None


@requires_docker
@pytest.mark.asyncio
async def test_instructions_for_the_customers_own_paid_invoice_say_it_is_paid(shop: Shop) -> None:
    contact = await shop.ledger.contact()
    await shop.invoice(contact, amount="29.90")
    paid_at = datetime.now(UTC) - timedelta(days=2)
    paid_id, paid = await shop.invoice(contact, status="paid", paid_at=paid_at)
    await shop.ledger.ledger_payment(paid_id, received_at=paid_at)

    got = facts(await shop.run(contact, {"action": "instructions", "reference": paid}))

    assert got == [("payment_already_paid_1", f"Invoice {paid} is already paid.")]


@requires_docker
@pytest.mark.asyncio
async def test_a_claim_with_several_claimed_and_none_waiting_repeats_each_claim(
    shop: Shop,
) -> None:
    contact = await shop.ledger.contact()
    _, first = await shop.invoice(contact, status="proof_submitted", amount="29.90")
    _, second = await shop.invoice(
        contact, status="proof_submitted", amount="39.90", due_at=DUE + timedelta(days=1)
    )

    got = facts(await shop.run(contact, {"action": "claim_paid", "note": "paid again"}))

    assert got == [
        ("payment_claim_already_1", claimed_en(first, "MYR 29.90")),
        ("payment_claim_already_2", claimed_en(second, "MYR 39.90")),
    ]


@requires_docker
@pytest.mark.asyncio
async def test_the_longest_instructions_reply_shows_four_methods_and_fits_one_message(
    shop: Shop,
) -> None:
    """Five active methods with the longest details and instructions, five open invoices with the
    longest plan name and amount: the first four methods by sort order are shown, and the reply
    stays under one Telegram message (4096 characters) in both languages."""
    await shop.w.q(
        "DELETE FROM payment_methods WHERE client_id = %s AND kind = 'transfer'", shop.client
    )
    for order in range(5):
        await shop.method(
            "transfer",
            {
                "bank_name": "B" * 59 + str(order),
                "account_name": "N" * 80,
                "account_number": f"51200000000000000{order:03d}",
            },
            order=order + 1,
        )
    await shop.w.q(
        "UPDATE payment_methods SET instructions = %s WHERE client_id = %s",
        "I" * 500,
        shop.client,
    )
    contact = await shop.ledger.contact()
    for n in range(5):
        invoice_id, _ = await shop.invoice(
            contact, amount="9999999999.99", due_at=DUE + timedelta(days=n)
        )
        await shop.w.q(
            "UPDATE invoices SET line_items = %s WHERE id = %s",
            Jsonb([{"description": "P" * 120, "quantity": 1, "unit_price": "9999999999.99"}]),
            invoice_id,
        )
    for locale in ("en", "ms"):
        got = facts(await shop.run(contact, {"action": "instructions"}, locale=locale))
        ids = [fact_id for fact_id, _ in got]
        assert ids.count("payment_invoice_5") == 1
        assert [i for i in ids if i.startswith("payment_method_")] == [
            f"payment_method_{n}" for n in range(1, 5)
        ]
        text = "\n".join(t for _, t in got)
        for order in range(4):
            assert f"51200000000000000{order:03d}" in text
        assert "51200000000000000004" not in text
        assert len(text) < 4096, (locale, len(text))
