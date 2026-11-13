"""Payments 1, task 6: the reminder and invoice copy (pure functions, English and Malay).

Synthetic data only: a made-up bank, account 5120000000xx, made-up names.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from csa.payments.contracts import MethodOut
from csa.payments.copy import (
    invoice_line,
    locale_of,
    method_line,
    reference_line,
    reminder_text,
)
from csa.payments.invoices import InvoiceRecord

TZ = "Asia/Kuala_Lumpur"
REF = "K7QX-4MP"
ACCOUNT = "512000000012"
BRAND = "Kedai Contoh"
# 17:30 UTC on 19 December is 01:30 on 20 December in Kuala Lumpur: the local date is shown.
DUE = datetime(2026, 12, 19, 17, 30, tzinfo=UTC)

INVOICE = InvoiceRecord(
    id="inv_019a0000000000a1b2c3d4e5f6",
    client_id=uuid.uuid4(),
    contact_id=uuid.uuid4(),
    subscription_id=None,
    reference=REF,
    status="issued",
    amount=Decimal("1059.90"),
    currency="MYR",
    issued_at=datetime(2026, 12, 1, tzinfo=UTC),
    due_at=DUE,
    paid_at=None,
    period_start=None,
    period_end=None,
    plan_name="Pro Monthly",
    claim_note=None,
    claimed_at=None,
    method_kind=None,
)
OVERDUE = replace(INVOICE, status="overdue")

TRANSFER = MethodOut(
    id=uuid.uuid4(),
    kind="transfer",
    label="Bank",
    details={"bank_name": "Bank Contoh", "account_number": ACCOUNT, "account_name": "Kedai Contoh"},
    instructions="",
    is_active=True,
    sort_order=0,
    version=1,
)
QR = MethodOut(
    id=uuid.uuid4(),
    kind="qr",
    label="DuitNow",
    details={"duitnow_id": "DN-5120000099", "account_name": "Kedai Contoh Sdn Bhd"},
    instructions="Any bank app can pay a DuitNow ID.",
    is_active=True,
    sort_order=1,
    version=1,
)


def test_locale_of() -> None:
    assert locale_of("ms-MY") == "ms"
    assert locale_of("ms") == "ms"
    assert locale_of("en") == "en"
    assert locale_of("zh") == "en"
    assert locale_of(None) == "en"
    assert locale_of("") == "en"


def test_invoice_line_in_english() -> None:
    assert (
        invoice_line(INVOICE, "en", TZ)
        == "Invoice K7QX-4MP: MYR 1,059.90 for Pro Monthly, due 20 December 2026."
    )
    assert (
        invoice_line(OVERDUE, "en", TZ)
        == "Invoice K7QX-4MP: MYR 1,059.90 for Pro Monthly, was due 20 December 2026."
    )


def test_invoice_line_in_malay() -> None:
    assert (
        invoice_line(INVOICE, "ms", TZ)
        == "Invois K7QX-4MP: MYR 1,059.90 untuk Pro Monthly, kena bayar sebelum 20 Disember 2026."
    )
    assert (
        invoice_line(OVERDUE, "ms", ZoneInfo(TZ))
        == "Invois K7QX-4MP: MYR 1,059.90 untuk Pro Monthly, tarikh akhir 20 Disember 2026 "
        "dah lepas."
    )


def test_invoice_line_shows_the_reference_once_unmasked() -> None:
    for locale in ("en", "ms"):
        assert invoice_line(INVOICE, locale, TZ).count(REF) == 1


def test_method_lines_in_both_languages() -> None:
    assert (
        method_line(TRANSFER, "en")
        == "Bank transfer: Bank Contoh, account 512000000012, name Kedai Contoh."
    )
    assert (
        method_line(TRANSFER, "ms")
        == "Pindahan bank: Bank Contoh, akaun 512000000012, nama Kedai Contoh."
    )
    assert (
        method_line(QR, "en")
        == "DuitNow: DN-5120000099, name Kedai Contoh Sdn Bhd. Any bank app can pay a DuitNow ID."
    )
    assert (
        method_line(QR, "ms")
        == "DuitNow: DN-5120000099, nama Kedai Contoh Sdn Bhd. Any bank app can pay a DuitNow ID."
    )
    for locale in ("en", "ms"):
        assert method_line(TRANSFER, locale).count(ACCOUNT) == 1


def test_reference_line_one_and_several() -> None:
    assert (
        reference_line([REF], "en")
        == "Please put K7QX-4MP as the payment reference so we can match your payment."
    )
    assert (
        reference_line([REF], "ms")
        == "Letak K7QX-4MP sebagai rujukan bayaran ya, senang kami padankan."
    )
    assert (
        reference_line([REF, "M3QA-7ZK"], "en")
        == "Put each invoice's reference with its payment so we can match it."
    )
    assert (
        reference_line([REF, "M3QA-7ZK"], "ms")
        == "Letak rujukan setiap invois dengan bayarannya ya."
    )
    for locale in ("en", "ms"):
        assert reference_line([REF], locale).count(REF) == 1


EN_METHODS = (
    "Bank transfer: Bank Contoh, account 512000000012, name Kedai Contoh.\n"
    "DuitNow: DN-5120000099, name Kedai Contoh Sdn Bhd. Any bank app can pay a DuitNow ID.\n"
    "Please put K7QX-4MP as the payment reference so we can match your payment.\n"
    "If you've already paid, reply here and tell us when."
)
MS_METHODS = (
    "Pindahan bank: Bank Contoh, akaun 512000000012, nama Kedai Contoh.\n"
    "DuitNow: DN-5120000099, nama Kedai Contoh Sdn Bhd. Any bank app can pay a DuitNow ID.\n"
    "Letak K7QX-4MP sebagai rujukan bayaran ya, senang kami padankan.\n"
    "Kalau dah bayar, balas sini dan bagitahu bila ya."
)


# DUE is 01:30 on 20 December in Kuala Lumpur: the invoice's local due date is 2026-12-20.
BEFORE = date(2026, 12, 13)
ON_THE_DAY = date(2026, 12, 20)
A_DAY_LATE = date(2026, 12, 21)
A_WEEK_LATE = date(2026, 12, 27)
EN_FIRST = "Kedai Contoh: invoice K7QX-4MP for MYR 1,059.90"
MS_FIRST = "Kedai Contoh: invois K7QX-4MP untuk MYR 1,059.90"


@pytest.mark.parametrize(
    ("today", "first"),
    [
        (BEFORE, f"{EN_FIRST} is due on 20 December 2026."),
        (ON_THE_DAY, f"{EN_FIRST} is due today."),
        # A step-0 reminder built a day late (quiet hours, the daily cap) is no longer "today".
        (A_DAY_LATE, f"{EN_FIRST} was due on 20 December 2026 and is still unpaid."),
        (A_WEEK_LATE, f"{EN_FIRST} was due on 20 December 2026 and is still unpaid."),
    ],
)
def test_reminder_text_in_english(today: date, first: str) -> None:
    text = reminder_text(INVOICE, [TRANSFER, QR], today=today, brand=BRAND, locale="en", tz=TZ)
    assert text == f"{first}\n{EN_METHODS}"


@pytest.mark.parametrize(
    ("today", "first"),
    [
        (BEFORE, f"{MS_FIRST} kena bayar sebelum 20 Disember 2026."),
        (ON_THE_DAY, f"{MS_FIRST} kena bayar hari ini."),
        (
            A_DAY_LATE,
            f"{MS_FIRST}, tarikh akhir 20 Disember 2026 dah lepas dan masih belum dibayar.",
        ),
        (
            A_WEEK_LATE,
            f"{MS_FIRST}, tarikh akhir 20 Disember 2026 dah lepas dan masih belum dibayar.",
        ),
    ],
)
def test_reminder_text_in_malay(today: date, first: str) -> None:
    text = reminder_text(INVOICE, [TRANSFER, QR], today=today, brand=BRAND, locale="ms", tz=TZ)
    assert text == f"{first}\n{MS_METHODS}"


def test_the_due_date_is_compared_in_the_company_zone_not_utc() -> None:
    # 17:30 UTC on 19 December is already 20 December in Kuala Lumpur, so on the 20th it is today.
    assert DUE.date() == date(2026, 12, 19)
    text = reminder_text(INVOICE, [], today=ON_THE_DAY, brand=BRAND, locale="en", tz=TZ)
    assert text.startswith(f"{EN_FIRST} is due today.")
    in_utc = reminder_text(INVOICE, [], today=ON_THE_DAY, brand=BRAND, locale="en", tz="UTC")
    assert in_utc.startswith(f"{EN_FIRST} was due on 19 December 2026 and is still unpaid.")


def test_reminder_text_carries_the_account_once_and_the_reference_where_named() -> None:
    for locale in ("en", "ms"):
        text = reminder_text(
            INVOICE, [TRANSFER, QR], today=ON_THE_DAY, brand=BRAND, locale=locale, tz=TZ
        )
        assert text.count(ACCOUNT) == 1
        # In the first line and in the reference line, unmasked both times.
        assert text.count(REF) == 2
        assert "*" not in text


def test_reminder_text_without_methods_still_names_the_reference_and_the_paid_line() -> None:
    text = reminder_text(INVOICE, [], today=BEFORE, brand=BRAND, locale="en", tz=TZ)
    assert text == (
        "Kedai Contoh: invoice K7QX-4MP for MYR 1,059.90 is due on 20 December 2026.\n"
        "Please put K7QX-4MP as the payment reference so we can match your payment.\n"
        "If you've already paid, reply here and tell us when."
    )


def test_no_customer_text_or_staff_field_reaches_the_copy() -> None:
    claimed = replace(INVOICE, claim_note="I paid on Tuesday via Maybank, see 0123456789")
    text = reminder_text(claimed, [TRANSFER], today=ON_THE_DAY, brand=BRAND, locale="en", tz=TZ)
    assert "Tuesday" not in text and "0123456789" not in text
    assert "Tuesday" not in invoice_line(claimed, "en", TZ)
