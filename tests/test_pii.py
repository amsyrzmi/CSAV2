"""PII redaction behavior used by channel ingress (LG03)."""

from csa.core.pii import real_email, redact


def test_redacts_email_malaysian_phone_and_valid_card_number() -> None:
    text = "Email me@test.example, call +60 12-345 6789, card 4111 1111 1111 1111."

    assert redact(text) == "Email [EMAIL_0], call [PHONE_0], card [CARD_0]."


def test_preserves_non_luhn_tracking_number_and_reuses_placeholders() -> None:
    text = "Call 012-345-6789; 012-345-6789. Tracking 1234 5678 9012 3456."

    assert redact(text) == "Call [PHONE_0]; [PHONE_0]. Tracking 1234 5678 9012 3456."


def test_a_placeholder_maps_back_to_the_address_redact_numbered_it_for() -> None:
    raw = "Use b@test.example, not a@test.example (b@test.example again), call 012-345-6789"
    assert redact(raw) == "Use [EMAIL_0], not [EMAIL_1] ([EMAIL_0] again), call [PHONE_0]"
    assert real_email("[EMAIL_0]", raw) == "b@test.example"
    assert real_email("[EMAIL_1]", raw) == "a@test.example"
    assert real_email("[EMAIL_2]", raw) is None


def test_only_an_address_that_is_really_in_the_message_is_accepted() -> None:
    raw = "sure, Name@Test.example"
    assert real_email("name@test.example", raw) == "Name@Test.example"  # the message's own spelling
    assert real_email("invented@test.example", raw) is None
    assert real_email("[EMAIL_0]", "no address here") is None
    assert real_email("[PHONE_0]", "call 012-345-6789") is None
