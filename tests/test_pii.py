"""PII redaction behavior used by channel ingress (LG03)."""

from csa.core.pii import redact


def test_redacts_email_malaysian_phone_and_valid_card_number() -> None:
    text = "Email me@test.example, call +60 12-345 6789, card 4111 1111 1111 1111."

    assert redact(text) == "Email [EMAIL_0], call [PHONE_0], card [CARD_0]."


def test_preserves_non_luhn_tracking_number_and_reuses_placeholders() -> None:
    text = "Call 012-345-6789; 012-345-6789. Tracking 1234 5678 9012 3456."

    assert redact(text) == "Call [PHONE_0]; [PHONE_0]. Tracking 1234 5678 9012 3456."
