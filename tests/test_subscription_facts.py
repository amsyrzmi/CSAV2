"""What the customer bot tells a customer about plans and their own subscription (pure)."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, get_args
from uuid import uuid4

from csa.agents.staged import subscription_facts as subscription_facts_module
from csa.agents.staged.subscription_facts import (
    LIVE_STATUSES,
    NONE_TEXT_MS,
    REQUESTABLE_STATUSES,
    SIGNUP_DATA_NOTE,
    SIGNUP_DATA_NOTE_MS,
    SIGNUP_EMAIL_NOTE,
    SIGNUP_EMAIL_NOTE_MS,
    SUBSCRIPTION_SOURCE,
    VERIFIED_PHONE,
    VERIFIED_PHONE_MS,
    SignupField,
    SignupKind,
    plan_facts,
    request_fact,
    signup_fact,
    subscription_facts,
)
from csa.contracts.facts import Fact

ROOT = Path(__file__).resolve().parents[1]
ZONE = "Asia/Kuala_Lumpur"
RENEWS = datetime(2026, 12, 1, 4, 0, tzinfo=UTC)


def row(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": uuid4(),
        "plan": "basic",
        "plan_name": "Basic",
        "billing_interval": "month",
        "status": "active",
        "price": Decimal("10.00"),
        "currency": "MYR",
        "renews_at": RENEWS,
        "cancel_at_period_end": False,
        "resume_at": None,
        "pending_plan": None,
        "pending_plan_name": None,
        "pending_effective_at": None,
    }
    return {**base, **fields}


def texts(rows: list[dict[str, Any]]) -> list[str]:
    return [fact.text for fact in subscription_facts(rows, ZONE)]


def test_the_statuses_are_the_live_and_the_requestable_ones() -> None:
    assert LIVE_STATUSES == ("active", "paused", "pending")
    assert REQUESTABLE_STATUSES == frozenset({"active", "paused"})
    assert SUBSCRIPTION_SOURCE == "subscription_agent"


def test_plan_facts_one_per_plan_with_features_and_interval() -> None:
    facts = plan_facts(
        [
            {
                "code": "basic",
                "name": "Basic",
                "price": Decimal("10"),
                "currency": "MYR",
                "billing_interval": "month",
                "features": ["5 GB data", "Calls"],
            },
            {
                "code": "year",
                "name": "Yearly",
                "price": "99.5",
                "currency": "MYR",
                "billing_interval": "year",
                "features": [],
            },
        ]
    )
    assert [f.id for f in facts] == ["subscription_plan_1", "subscription_plan_2"]
    assert facts[0].text == "Basic: MYR 10.00 a month. Includes: 5 GB data; Calls."
    assert facts[1].text == "Yearly: MYR 99.50 a year."
    assert {f.source for f in facts} == {SUBSCRIPTION_SOURCE}


def _plan(features: Any) -> dict[str, Any]:
    return {
        "code": "basic",
        "name": "Basic",
        "price": Decimal("10"),
        "currency": "MYR",
        "billing_interval": "month",
        "features": features,
    }


def test_a_feature_ending_in_a_full_stop_does_not_double_it() -> None:
    (fact,) = plan_facts([_plan(["5 GB data.", "Calls. ", "Texts"])])
    assert fact.text == "Basic: MYR 10.00 a month. Includes: 5 GB data; Calls; Texts."
    (only_stops,) = plan_facts([_plan(["."])])
    assert only_stops.text == "Basic: MYR 10.00 a month."


def test_a_bare_string_features_value_is_one_feature() -> None:
    (fact,) = plan_facts([_plan("5 GB data.")])
    assert fact.text == "Basic: MYR 10.00 a month. Includes: 5 GB data."


def test_an_active_plan_renews_and_a_period_end_cancel_ends() -> None:
    assert texts([row()]) == ["Your plan: Basic, MYR 10.00 a month. It renews on 01 Dec 2026."]
    assert texts([row(cancel_at_period_end=True)]) == [
        "Your plan: Basic, MYR 10.00 a month. It ends on 01 Dec 2026 and will not renew."
    ]


def test_paused_with_and_without_a_resume_date() -> None:
    assert texts([row(status="paused")]) == ["Your plan: Basic is paused."]
    assert texts([row(status="paused", resume_at=date(2027, 1, 5))]) == [
        "Your plan: Basic is paused until 05 Jan 2027."
    ]


def test_a_pending_plan_is_waiting_to_be_started_with_no_date() -> None:
    # No opening invoice (payments off, or a free plan): the text is as before payments.
    assert texts([row(status="pending", renews_at=None)]) == [
        "Your plan: Basic, MYR 10.00 a month, is waiting to be started by the team. "
        "Nothing has been charged."
    ]


def pending(invoice_status: str | None, reference: str | None = "KDMW-7AS") -> dict[str, Any]:
    return row(
        status="pending",
        renews_at=None,
        opening_invoice_status=invoice_status,
        opening_invoice_reference=reference if invoice_status else None,
    )


def test_a_pending_plan_with_an_open_opening_invoice_waits_for_the_first_payment() -> None:
    for status in ("issued", "overdue", "cash_pending"):
        assert texts([pending(status)]) == [
            "Your plan: Basic, MYR 10.00 a month, starts when your first payment is confirmed. "
            "Invoice KDMW-7AS is waiting for payment."
        ], status


def test_a_pending_plan_whose_invoice_the_customer_says_is_paid_is_not_confirmed_yet() -> None:
    assert texts([pending("proof_submitted")]) == [
        "Your plan: Basic, MYR 10.00 a month, starts when your payment is confirmed. "
        "You told us you paid; it isn't confirmed yet."
    ]


def test_a_pending_plan_never_says_nothing_was_charged_when_an_invoice_waits() -> None:
    for status in ("issued", "overdue", "proof_submitted", "cash_pending"):
        (text,) = texts([pending(status)])
        assert "charged" not in text and "by the team" not in text, status
    # Without a reference the invoice cannot be named: the text is the one without an invoice.
    assert texts([pending("issued", reference=None)]) == texts([pending(None)])


def test_a_cancelled_plan_says_so_and_an_unknown_status_gives_no_fact() -> None:
    assert texts([row(status="cancelled")]) == ["Your plan: Basic was cancelled."]
    assert texts([row(status="mystery")]) == []


def test_a_scheduled_change_is_added_with_its_date() -> None:
    changing = row(
        pending_plan="pro",
        pending_plan_name="Pro",
        pending_effective_at=datetime(2026, 12, 1, 4, 0, tzinfo=UTC),
    )
    assert texts([changing]) == [
        "Your plan: Basic, MYR 10.00 a month. It renews on 01 Dec 2026. "
        "It changes to Pro on 01 Dec 2026."
    ]
    undated = row(pending_plan="pro", pending_plan_name=None)
    assert texts([undated])[0].endswith(" It is set to change to pro.")


def test_without_a_plan_row_the_interval_is_left_out() -> None:
    orphan = row(plan_name=None, billing_interval=None)
    assert texts([orphan]) == ["Your plan: basic, MYR 10.00. It renews on 01 Dec 2026."]


def test_several_rows_give_one_fact_each_newest_first_as_given() -> None:
    facts = subscription_facts([row(plan_name="Pro"), row(plan_name="Basic")], ZONE)
    assert [f.id for f in facts] == ["subscription_1", "subscription_2"]
    assert facts[0].text.startswith("Your plan: Pro,")


def test_no_rows_is_the_no_subscription_sentence() -> None:
    facts = subscription_facts([], ZONE)
    assert [(f.id, f.text) for f in facts] == [
        ("subscription_none", "I can't find a subscription linked to this chat.")
    ]


def test_request_facts_use_the_exact_copy() -> None:
    def text(*args: Any, **kwargs: Any) -> str:
        return request_fact(*args, **kwargs).text

    assert text("passed", action="request_pause", name="Basic") == (
        "Your request to pause Basic was passed to our staff to decide. Nothing has changed yet."
    )
    assert text("passed", action="request_cancel", name="Basic") == (
        "Your request to cancel Basic was passed to our staff to decide. Nothing has changed yet."
    )
    assert text("passed", action="request_change", name="Basic", new_name="Pro") == (
        "Your request to change Basic to Pro was passed to our staff to decide. "
        "Nothing has changed yet."
    )
    assert text("which", names=["Pro", "Basic"]) == (
        "This chat has more than one plan: Pro, Basic. Which one do you mean?"
    )
    assert text("which", names=["Basic"]) == (
        "I can't find a plan by that name on this chat. "
        "The plan I can see is Basic. Is that the one you mean?"
    )
    assert text("none") == "I can't find a subscription linked to this chat."
    assert text("already_ending", name="Basic", ends=RENEWS) == (
        "Basic is already set to end on 01 Dec 2026 and will not renew."
    )
    assert text("already_paused", name="Basic") == "Basic is already paused."
    assert text("waiting", name="Basic") == (
        "Basic has not started yet, so it can't be paused, cancelled or changed yet. The team "
        "can help with any question about it."
    )
    assert [request_fact(kind).id for kind in ("none", "which")] == [
        "subscription_none",
        "subscription_which",
    ]
    assert request_fact("passed", action="request_pause", name="Basic").id == "subscription_passed"


def test_fact_ids_carry_no_subscription_id() -> None:
    rows = [row(), row(status="paused")]
    ids = [f.id for f in subscription_facts(rows, ZONE)]
    ids += [f.id for f in plan_facts([{"code": "a", "name": "A", "price": 1, "currency": "MYR"}])]
    ids += [request_fact("none").id, request_fact("which", names=["A", "B"]).id]
    for fact_id in ids:
        assert fact_id in {
            "subscription_1",
            "subscription_2",
            "subscription_plan_1",
            "subscription_none",
            "subscription_which",
        }
    for one in rows:
        assert not any(str(one["id"]) in fact_id for fact_id in ids)


def test_the_live_sql_allowlist_names_pending() -> None:
    sql = (ROOT / "db/sql/agents/subscription_list.sql").read_text()
    assert "'active'" in sql and "'paused'" in sql and "'pending'" in sql
    assert "client_id" in sql and "contact_id" in sql


def test_both_subscription_reads_carry_the_opening_invoice() -> None:
    for name in ("subscription_list.sql", "subscription_get.sql"):
        sql = (ROOT / "db/sql/agents" / name).read_text()
        assert "LATERAL" in sql, name
        assert "opening_invoice_status" in sql and "opening_invoice_reference" in sql, name


def test_signup_facts_use_the_exact_copy() -> None:
    def text(*args: Any, **kwargs: Any) -> str:
        return signup_fact(*args, **kwargs).text

    assert text("missing", plan="Plan Besar", field="full name") == (
        "To sign up for Plan Besar I still need your full name."
    )
    assert text("missing", plan="Plan Besar", field="phone number") == (
        "To sign up for Plan Besar I still need your phone number."
    )
    assert SIGNUP_DATA_NOTE == (
        "We keep your name and phone number on your contact record so the team can set up "
        "your plan."
    )
    assert text("data_note") == SIGNUP_DATA_NOTE
    # The email clause is appended only on a turn that asks for the email.
    assert SIGNUP_EMAIL_NOTE == (
        " If you give us your email, we use it for payment reminders and booking messages."
    )
    assert text("data_note", with_email=False) == SIGNUP_DATA_NOTE
    assert text("data_note", with_email=True) == SIGNUP_DATA_NOTE + SIGNUP_EMAIL_NOTE
    # The kind and id stay the same with the clause.
    assert signup_fact("data_note", n=2, with_email=True).id == "signup_data_note_2"
    assert text("readback", plan="Plan Besar", name="Ali Bin Abu", phone="+60123456789") == (
        "Sign-up for Plan Besar: name Ali Bin Abu, phone +60123456789. Is that right? Reply "
        "yes to sign up. Nothing is filed until you do."
    )
    assert VERIFIED_PHONE == "the number already on this chat"
    assert text("readback", plan="Plan Besar", name="Ali Bin Abu", phone=VERIFIED_PHONE) == (
        "Sign-up for Plan Besar: name Ali Bin Abu, phone the number already on this chat. Is "
        "that right? Reply yes to sign up. Nothing is filed until you do."
    )
    assert text("phone_invalid") == (
        "That phone number does not look right. Please type it again with the area or country code."
    )
    assert text("filed", plan="Plan Besar") == (
        "Your sign-up for Plan Besar was received. The team will review it and tell you what "
        "happens next. Nothing is active yet."
    )
    assert text("filed", plan="Plan Besar", payments=True) == (
        "Your sign-up for Plan Besar was received. The team will review it; once it is approved "
        "you'll get an invoice with how to pay, and the plan starts when the payment is "
        "confirmed. Nothing is active yet."
    )
    assert text("filed_auto", plan="Plan Besar") == (
        "Your sign-up for Plan Besar is registered and waiting to be started by the team."
    )
    assert text("filed_auto_invoice", plan="Plan Besar", ref="KDMW-7AS") == (
        "Your sign-up for Plan Besar is registered. Invoice KDMW-7AS is ready: ask me how to "
        "pay. The plan starts when the payment is confirmed."
    )
    assert text("exists", plan="Plan Besar") == (
        "You already have a sign-up for Plan Besar waiting for the team."
    )
    assert text("has_plan", plan="Plan Besar") == "You already have Plan Besar."
    assert text("closed", plan="Plan Besar") == "Plan Besar is not taking new sign-ups right now."
    assert text("which") == "To sign up I still need to know which plan you want."
    assert text("try_later") == (
        "The sign-up could not be filed just now, so nothing was filed. Please try again in a "
        "little while."
    )


def test_the_sign_up_email_facts_and_read_back_use_the_exact_copy() -> None:
    def text(*args: Any, **kwargs: Any) -> str:
        return signup_fact(*args, **kwargs).text

    assert text("email_ask") == (
        "If you'd like payment reminders by email, tell me your email address, or say skip. "
        "We keep it on your contact record to send you messages about your plan and bookings."
    )
    # Said to the customer (it can be sent as it stands), never to the composer.
    assert text("email_invalid") == (
        "That email address does not look right. Please type it again, or say skip."
    )
    assert signup_fact("email_ask", n=2).id == "signup_email_ask_2"
    assert signup_fact("email_invalid").id == "signup_email_invalid_1"
    base: dict[str, Any] = {"plan": "Plan Besar", "name": "Ali Bin Abu", "phone": "+60123456789"}
    assert text("readback", **base, email="ali.abu@example.com", with_email=True) == (
        "Sign-up for Plan Besar: name Ali Bin Abu, phone +60123456789, email "
        "ali.abu@example.com. Is that right? Reply yes to sign up. Nothing is filed until you do."
    )
    assert text("readback", **base, email=None, with_email=True) == (
        "Sign-up for Plan Besar: name Ali Bin Abu, phone +60123456789, no email. Is that right? "
        "Reply yes to sign up. Nothing is filed until you do."
    )
    # Where the email was never asked (Telegram, payments off) the read-back names none at all.
    assert text("readback", **base, email="ali.abu@example.com") == (
        "Sign-up for Plan Besar: name Ali Bin Abu, phone +60123456789. Is that right? Reply "
        "yes to sign up. Nothing is filed until you do."
    )
    readback = signup_fact(
        "readback",
        plan="Plan Besar",
        name="Ali Bin Abu",
        phone="+60123456789",
        email="ali.abu@example.com",
        with_email=True,
    )
    assert "example" not in readback.id


def test_signup_fact_ids_are_constants_with_an_index() -> None:
    assert signup_fact("missing", plan="A", field="full name").id == "signup_missing_1"
    assert signup_fact("data_note", n=2).id == "signup_data_note_2"
    readback = signup_fact("readback", plan="A", name="Ali Bin Abu", phone="+60123456789")
    assert readback.id == "signup_readback_1"
    assert "Ali" not in readback.id and "60123" not in readback.id
    assert readback.source == SUBSCRIPTION_SOURCE
    # Approval is never claimed: no fact says approved, active or charged as done.
    for kind in ("filed", "filed_auto", "exists"):
        sentence = signup_fact(kind, plan="A").text  # type: ignore[arg-type]
        assert "approved" not in sentence and "is now active" not in sentence
    # Nothing claims a charge either way: a fact never says one was or was not made.
    for kind in ("filed", "filed_auto", "filed_auto_invoice"):
        for payments in (False, True):
            fact = signup_fact(kind, plan="A", ref="KDMW-7AS", payments=payments)  # type: ignore[arg-type]
            assert "charged" not in fact.text, (kind, payments)
    assert signup_fact("filed_auto_invoice", plan="A", ref="KDMW-7AS").id == (
        "signup_filed_auto_invoice_1"
    )


# --- Malay (`text_ms`): a write's facts can go out word for word on a Malay turn ---------------

FORBIDDEN_MS = (" anda ", "Anda ", "sila", "Sila", "pihak kami", "Pihak kami")


def _sentences(text: str) -> list[str]:
    """The English sentences of a fact (split after '.' or '?')."""
    return [s for s in (p.strip() for p in re.split(r"(?<=[.?])\s+", text)) if s]


def _assert_malay(fact: Fact) -> str:
    """The fact's Malay: set, holding none of its English sentences or the forbidden words."""
    assert fact.text_ms, fact.id
    for sentence in _sentences(fact.text):
        assert sentence not in fact.text_ms, (fact.id, sentence)
    for word in FORBIDDEN_MS:
        assert word not in fact.text_ms, (fact.id, word)
    for word in (" the ", "Please", "Nothing", "Reply", "Your ", "You ", "Invoice", "sign-up"):
        assert word not in fact.text_ms, (fact.id, word)
    return fact.text_ms


def test_request_facts_carry_the_malay_copy_and_keep_the_english() -> None:
    cases: list[tuple[dict[str, Any], str]] = [
        (
            {"kind": "passed", "action": "request_pause", "name": "Basic"},
            "Permintaan awak nak hentikan sementara Basic dah dihantar kat staf kami untuk "
            "diputuskan. Belum ada apa-apa yang berubah lagi.",
        ),
        (
            {"kind": "passed", "action": "request_cancel", "name": "Basic"},
            "Permintaan awak nak batalkan Basic dah dihantar kat staf kami untuk diputuskan. "
            "Belum ada apa-apa yang berubah lagi.",
        ),
        (
            {"kind": "passed", "action": "request_change", "name": "Basic", "new_name": "Pro"},
            "Permintaan awak nak tukar Basic ke Pro dah dihantar kat staf kami untuk "
            "diputuskan. Belum ada apa-apa yang berubah lagi.",
        ),
        (
            {"kind": "which", "names": ["Pro", "Basic"]},
            "Chat ni ada lebih dari satu pelan: Pro, Basic. Yang mana satu awak maksudkan?",
        ),
        (
            {"kind": "which", "names": ["Basic"]},
            "Saya tak jumpa pelan dengan nama tu dalam chat ni. Pelan yang saya nampak ialah "
            "Basic. Yang ni ke yang awak maksudkan?",
        ),
        ({"kind": "none"}, "Saya tak jumpa langganan yang berkait dengan chat ni."),
        (
            {"kind": "already_ending", "name": "Basic", "ends": RENEWS},
            "Basic memang dah ditetapkan untuk tamat pada 1 Disember 2026 dan takkan diperbaharui.",
        ),
        (
            {"kind": "already_ending", "name": "Basic"},
            "Basic memang dah ditetapkan untuk tamat dan takkan diperbaharui.",
        ),
        ({"kind": "already_paused", "name": "Basic"}, "Basic memang dah dihentikan sementara."),
        (
            {"kind": "waiting", "name": "Basic"},
            "Basic belum bermula lagi, jadi belum boleh dihentikan sementara, dibatalkan atau "
            "ditukar. Staf kami boleh tolong kalau awak ada apa-apa soalan pasal pelan ni.",
        ),
    ]
    for fields, malay in cases:
        english = request_fact(**fields)
        fact = request_fact(**fields, ms=True)
        assert fact.text == english.text and fact.id == english.id, fields
        assert english.text_ms is None, fields  # a read's fact never carries Malay
        assert _assert_malay(fact) == malay, fields
    assert NONE_TEXT_MS == "Saya tak jumpa langganan yang berkait dengan chat ni."


def test_a_which_question_takes_the_malay_names_of_two_plans_that_share_a_name() -> None:
    names = ["Pro (from 01 Dec 2026)", "Pro (from 02 Dec 2026)"]
    malay = ["Pro (dari 1 Disember 2026)", "Pro (dari 2 Disember 2026)"]
    fact = request_fact("which", names=names, names_ms=lambda: malay, ms=True)
    assert fact.text == request_fact("which", names=names).text
    assert _assert_malay(fact) == (
        "Chat ni ada lebih dari satu pelan: Pro (dari 1 Disember 2026), Pro (dari 2 Disember "
        "2026). Yang mana satu awak maksudkan?"
    )
    assert "from" not in fact.text_ms  # type: ignore[operator]


def test_the_request_verbs_in_malay_cover_the_english_ones() -> None:
    assert set(subscription_facts_module._VERBS_MS) == set(subscription_facts_module._VERBS)
    # An action with no verb reads as a change, in both languages.
    fact = request_fact("passed", action=None, name="Basic", ms=True)
    assert fact.text.startswith("Your request to change Basic was passed")
    assert fact.text_ms is not None and fact.text_ms.startswith("Permintaan awak nak tukar Basic")


def test_signup_facts_carry_the_malay_copy_and_keep_the_english() -> None:
    plan = {"plan": "Plan Besar"}
    base: dict[str, Any] = {"plan": "Plan Besar", "name": "Ali Bin Abu", "phone": "+60123456789"}
    readback_tail = (
        "Betul ke? Balas ya untuk daftar. Tak ada apa-apa yang didaftarkan selagi awak belum "
        "balas ya."
    )
    cases: list[tuple[str, dict[str, Any], str]] = [
        (
            "missing",
            {**plan, "field": "full name"},
            "Untuk daftar Plan Besar, saya masih perlukan nama penuh awak.",
        ),
        (
            "missing",
            {**plan, "field": "phone number"},
            "Untuk daftar Plan Besar, saya masih perlukan nombor telefon awak.",
        ),
        ("data_note", {}, SIGNUP_DATA_NOTE_MS),
        ("data_note", {"with_email": True}, SIGNUP_DATA_NOTE_MS + SIGNUP_EMAIL_NOTE_MS),
        (
            "readback",
            base,
            f"Pendaftaran Plan Besar: nama Ali Bin Abu, telefon +60123456789. {readback_tail}",
        ),
        (
            "readback",
            {**base, "phone": VERIFIED_PHONE},
            "Pendaftaran Plan Besar: nama Ali Bin Abu, telefon nombor yang dah ada dalam chat "
            f"ni. {readback_tail}",
        ),
        (
            "readback",
            {**base, "email": "ali.abu@example.com", "with_email": True},
            "Pendaftaran Plan Besar: nama Ali Bin Abu, telefon +60123456789, e-mel "
            f"ali.abu@example.com. {readback_tail}",
        ),
        (
            "readback",
            {**base, "email": None, "with_email": True},
            "Pendaftaran Plan Besar: nama Ali Bin Abu, telefon +60123456789, tak ada e-mel. "
            f"{readback_tail}",
        ),
        (
            "readback",
            {**base, "email": "ali.abu@example.com"},
            f"Pendaftaran Plan Besar: nama Ali Bin Abu, telefon +60123456789. {readback_tail}",
        ),
        (
            "phone_invalid",
            {},
            "Nombor telefon tu macam tak betul. Taip sekali lagi dengan kod kawasan atau kod "
            "negara ya.",
        ),
        (
            "email_ask",
            {},
            "Kalau awak nak peringatan bayaran melalui e-mel, bagitahu alamat e-mel awak, atau "
            "cakap skip. Kami simpan dalam rekod pelanggan untuk hantar mesej pasal pelan dan "
            "tempahan awak.",
        ),
        (
            "email_invalid",
            {},
            "Alamat e-mel tu macam tak betul. Taip sekali lagi ya, atau cakap skip.",
        ),
        (
            "filed",
            plan,
            "Pendaftaran awak untuk Plan Besar dah diterima. Staf kami akan semak dan bagitahu "
            "awak apa langkah seterusnya. Belum ada apa-apa yang aktif lagi.",
        ),
        (
            "filed",
            {**plan, "payments": True},
            "Pendaftaran awak untuk Plan Besar dah diterima. Staf kami akan semak dulu; lepas "
            "diluluskan, awak akan dapat invois dengan cara nak bayar, dan pelan bermula bila "
            "bayaran dah disahkan. Belum ada apa-apa yang aktif lagi.",
        ),
        (
            "filed_auto",
            plan,
            "Pendaftaran awak untuk Plan Besar dah direkodkan dan tengah tunggu staf kami mulakan.",
        ),
        (
            "filed_auto_invoice",
            {**plan, "ref": "KDMW-7AS"},
            "Pendaftaran awak untuk Plan Besar dah direkodkan. Invois KDMW-7AS dah sedia: tanya "
            "saya cara nak bayar. Pelan bermula bila bayaran dah disahkan.",
        ),
        (
            "exists",
            plan,
            "Awak dah ada pendaftaran untuk Plan Besar yang tengah tunggu staf kami.",
        ),
        ("has_plan", plan, "Awak dah ada Plan Besar."),
        ("closed", plan, "Plan Besar tak buka pendaftaran baru buat masa ni."),
        ("which", {}, "Untuk daftar, saya masih perlu tahu pelan mana yang awak nak."),
        (
            "try_later",
            {},
            "Pendaftaran tak dapat dihantar tadi, jadi tak ada apa-apa yang didaftarkan. Cuba "
            "semula sekejap lagi ya.",
        ),
    ]
    assert {kind for kind, _, _ in cases} == set(get_args(SignupKind))
    for kind, fields, malay in cases:
        english = signup_fact(kind, 2, **fields)  # type: ignore[arg-type]
        fact = signup_fact(kind, 2, **fields, ms=True)  # type: ignore[arg-type]
        assert fact.text == english.text and fact.id == english.id, (kind, fields)
        assert english.text_ms is None, kind
        assert _assert_malay(fact) == malay, (kind, fields)


def test_the_malay_data_note_carries_the_email_clause_exactly_when_the_english_does() -> None:
    assert SIGNUP_DATA_NOTE_MS == (
        "Kami simpan nama dan nombor telefon awak dalam rekod pelanggan, supaya staf kami boleh "
        "sediakan pelan awak."
    )
    # Same promise as the English: reminders and booking messages, no receipts.
    assert SIGNUP_EMAIL_NOTE_MS == (
        " Kalau awak bagi e-mel, kami guna untuk peringatan bayaran dan mesej tempahan."
    )
    for with_email in (False, True):
        fact = signup_fact("data_note", with_email=with_email, ms=True)
        assert fact.text_ms is not None
        assert fact.text.endswith(SIGNUP_EMAIL_NOTE) is with_email
        assert fact.text_ms.endswith(SIGNUP_EMAIL_NOTE_MS) is with_email
        assert fact.text_ms.startswith(SIGNUP_DATA_NOTE_MS)


def test_the_malay_sign_up_maps_cover_their_english_values() -> None:
    from csa.subscriptions.contracts import Interval

    assert set(subscription_facts_module._FIELDS_MS) == set(get_args(SignupField))
    assert set(subscription_facts_module._PER_MS) == set(get_args(Interval))
    assert VERIFIED_PHONE_MS == "nombor yang dah ada dalam chat ni"


def test_plan_facts_carry_malay_only_where_they_can_go_out_word_for_word() -> None:
    rows = [
        {**_plan(["5 GB data", "Calls"])},
        {
            "code": "y",
            "name": "Yearly",
            "price": "99.5",
            "currency": "MYR",
            "billing_interval": "year",
        },
        {"code": "z", "name": "Zero", "price": 0, "currency": "MYR", "billing_interval": None},
    ]
    english = plan_facts(rows)
    malay = plan_facts(rows, ms=True)
    assert [f.text for f in malay] == [f.text for f in english]
    assert [f.text_ms for f in english] == [None, None, None]
    assert [_assert_malay(f) for f in malay[:2]] == [
        "Basic: MYR 10.00 sebulan. Termasuk: 5 GB data; Calls.",
        "Yearly: MYR 99.50 setahun.",
    ]
    # With no interval and no features there is no English word to put in Malay.
    assert malay[2].text_ms == malay[2].text == "Zero: MYR 0.00."


def test_a_malay_failure_leaves_the_english_fact_whole(monkeypatch: Any) -> None:
    from structlog.testing import capture_logs

    def broken(*_: Any, **__: Any) -> str:
        raise KeyError("boom")

    monkeypatch.setattr(subscription_facts_module, "local_date", broken)
    monkeypatch.setattr(subscription_facts_module, "_signup_ms", broken)
    with capture_logs() as logs:
        ending = request_fact("already_ending", name="Basic", ends=RENEWS, ms=True)
        filed = signup_fact("filed", plan="Plan Besar", ms=True)
        names = request_fact("which", names=["Pro", "Basic"], names_ms=broken, ms=True)
    assert ending.text == "Basic is already set to end on 01 Dec 2026 and will not renew."
    assert filed.text.startswith("Your sign-up for Plan Besar was received.")
    assert names.text == "This chat has more than one plan: Pro, Basic. Which one do you mean?"
    assert (ending.text_ms, filed.text_ms, names.text_ms) == (None, None, None)
    failed = [e for e in logs if e["event"] == "subscription_malay_failed"]
    assert [e["fact"] for e in failed] == [
        "subscription_already_ending",
        "signup_filed_1",
        "subscription_which",
    ]
    assert {e["error_type"] for e in failed} == {"KeyError"}
    assert {e["log_level"] for e in failed} == {"warning"}
