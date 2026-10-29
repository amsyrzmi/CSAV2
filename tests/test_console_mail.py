"""Invitation and reset mail: templates, failure categories and configuration, without a relay."""

from __future__ import annotations

import smtplib
import threading
from datetime import UTC, datetime
from typing import Any

import pytest
from csa.console.mail_templates import invitation_mail, reset_mail
from csa.console.mailer import Mailer, OutgoingMail, SmtpTransport
from csa.core.settings import Settings
from structlog.testing import capture_logs

LINK = "https://console.example.test/invite#token=synthetic-token-value"
EXPIRES = datetime(2026, 10, 27, 1, 15, tzinfo=UTC)
SENDER = "FourStacks <no-reply@fourstacks.my>"
BUTTON = (
    "display:inline-block;padding:10px 18px;background:#1f2933;color:#ffffff;"
    "text-decoration:none;border-radius:6px;"
)
BODY = (
    "margin:0;padding:24px;font-family:Arial,Helvetica,sans-serif;font-size:15px;"
    "line-height:1.5;color:#1f2933;"
)


def test_invitation_template_snapshot() -> None:
    mail = invitation_mail(
        to="aminah@example.test",
        invitee_name="Aminah <Synthetic>",
        inviter_name="Synthetic Owner",
        business_name="Kedai Ali & Anak",
        link=LINK,
        expires_at=EXPIRES,
        tz="Asia/Jakarta",
    )
    assert mail.to == "aminah@example.test"
    assert mail.subject == "You're invited to Kedai Ali & Anak on FourStacks"
    assert mail.text == (
        "Hello Aminah <Synthetic>,\n"
        "\n"
        "Synthetic Owner has invited you to join Kedai Ali & Anak on FourStacks.\n"
        "\n"
        "Open this link to set your password and sign in:\n"
        f"{LINK}\n"
        "\n"
        "The link expires on 27 Oct 2026, 08:15. "
        "After that, ask Synthetic Owner for a new invitation.\n"
        "\n"
        "If you did not expect this invitation, you can ignore this email. "
        "Nothing changes until you open the link and set a password.\n"
        "\n"
        "FourStacks\n"
    )
    assert mail.html == (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        f'<body style="{BODY}">\n'
        "<p>Hello Aminah &lt;Synthetic&gt;,</p>\n"
        "<p>Synthetic Owner has invited you to join <strong>Kedai Ali &amp; Anak</strong>"
        " on FourStacks.</p>\n"
        f'<p><a href="{LINK}" style="{BUTTON}">Accept invitation</a></p>\n'
        f"<p>If the button does not work, copy this link into your browser:<br>{LINK}</p>\n"
        "<p>The link expires on 27 Oct 2026, 08:15. "
        "After that, ask Synthetic Owner for a new invitation.</p>\n"
        "<p>If you did not expect this invitation, you can ignore this email. "
        "Nothing changes until you open the link and set a password.</p>\n"
        "<p>FourStacks</p>\n"
        "</body>\n"
        "</html>\n"
    )


def test_invitation_subject_keeps_one_line() -> None:
    mail = invitation_mail(
        to="aminah@example.test",
        invitee_name="Aminah",
        inviter_name="Synthetic Owner",
        business_name="Kedai\r\nBcc: someone@example.test",
        link=LINK,
        expires_at=EXPIRES,
        tz="Asia/Kuala_Lumpur",
    )
    assert mail.subject == "You're invited to Kedai Bcc: someone@example.test on FourStacks"


def test_reset_template_snapshot() -> None:
    mail = reset_mail(to="aminah@example.test", name="Aminah", link=LINK, expires_at=EXPIRES)
    assert mail.to == "aminah@example.test"
    assert mail.subject == "Reset your FourStacks password"
    assert mail.text == (
        "Hello Aminah,\n"
        "\n"
        "A FourStacks administrator has started a password reset for your account.\n"
        "\n"
        "Open this link to choose a new password:\n"
        f"{LINK}\n"
        "\n"
        "The link expires on 27 Oct 2026, 09:15.\n"
        "\n"
        "If you did not expect this email, you can ignore it. "
        "Your password stays the same until you choose a new one.\n"
        "\n"
        "FourStacks\n"
    )
    assert mail.html == (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        f'<body style="{BODY}">\n'
        "<p>Hello Aminah,</p>\n"
        "<p>A FourStacks administrator has started a password reset for your account.</p>\n"
        f'<p><a href="{LINK}" style="{BUTTON}">Choose a new password</a></p>\n'
        f"<p>If the button does not work, copy this link into your browser:<br>{LINK}</p>\n"
        "<p>The link expires on 27 Oct 2026, 09:15.</p>\n"
        "<p>If you did not expect this email, you can ignore it. "
        "Your password stays the same until you choose a new one.</p>\n"
        "<p>FourStacks</p>\n"
        "</body>\n"
        "</html>\n"
    )


def test_dates_drop_the_leading_zero_of_the_day() -> None:
    mail = reset_mail(
        to="aminah@example.test",
        name="Aminah",
        link=LINK,
        expires_at=datetime(2026, 11, 4, 16, 5, tzinfo=UTC),
    )
    assert "The link expires on 5 Nov 2026, 00:05." in mail.text


class FakeTransport:
    def __init__(self, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.sent: list[tuple[OutgoingMail, str]] = []

    def send(self, mail: OutgoingMail, sender: str) -> None:
        if self.failure is not None:
            raise self.failure
        self.sent.append((mail, sender))


MAIL = OutgoingMail(
    to="aminah@example.test", subject="Synthetic subject", text=f"Open {LINK}", html=LINK
)


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        (smtplib.SMTPAuthenticationError(535, b"synthetic"), "smtp_auth_failed"),
        (smtplib.SMTPRecipientsRefused({"aminah@example.test": (550, b"no")}), "smtp_refused"),
        (smtplib.SMTPSenderRefused(553, b"no", "no-reply@fourstacks.my"), "smtp_refused"),
        (smtplib.SMTPDataError(554, b"synthetic"), "smtp_refused"),
        (TimeoutError("synthetic"), "smtp_timeout"),
        (smtplib.SMTPServerDisconnected("synthetic"), "smtp_error"),
        (smtplib.SMTPNotSupportedError("STARTTLS extension not supported"), "smtp_error"),
        (ConnectionRefusedError("synthetic"), "smtp_error"),
        (ValueError("synthetic header failure"), "smtp_error"),
    ],
)
async def test_mailer_maps_exceptions_to_categories(failure: Exception, category: str) -> None:
    transport = FakeTransport(failure)
    with capture_logs() as logs:
        outcome = await Mailer(transport, SENDER).send(MAIL)
    assert outcome == category
    assert logs == [
        {"event": "console_mail_not_sent", "category": category, "log_level": "warning"}
    ]
    assert "aminah@example.test" not in repr(logs) and "#token=" not in repr(logs)


async def test_mailer_sends_through_the_transport() -> None:
    transport = FakeTransport()
    with capture_logs() as logs:
        assert await Mailer(transport, SENDER).send(MAIL) == "sent"
    assert transport.sent == [(MAIL, SENDER)]
    assert logs == []


async def test_slow_relay_is_bounded_and_reported_as_timeout() -> None:
    release = threading.Event()

    class SlowTransport:
        def send(self, mail: OutgoingMail, sender: str) -> None:
            release.wait(5)

    try:
        with capture_logs() as logs:
            outcome = await Mailer(SlowTransport(), SENDER, limit=0.05).send(MAIL)
    finally:
        release.set()
    assert outcome == "smtp_timeout"
    assert logs == [
        {"event": "console_mail_not_sent", "category": "smtp_timeout", "log_level": "warning"}
    ]


def test_mailer_limit_defaults_to_fifteen_seconds() -> None:
    assert Mailer(None, SENDER).limit == 15


async def test_disabled_mailer_reports_smtp_disabled() -> None:
    with capture_logs() as logs:
        assert await Mailer(None, SENDER).send(MAIL) == "smtp_disabled"
    assert logs == [
        {"event": "console_mail_not_sent", "category": "smtp_disabled", "log_level": "info"}
    ]


def configured(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "ENV": "ci",
        "DATABASE_URL": "postgresql://csa_app:synthetic@localhost:1/disposable_fixture",
        "LG_DATABASE_URL": "postgresql://csa_app:synthetic@localhost:1/disposable_fixture",
        "GROQ_API_KEY": "synthetic-no-provider-access",
        "PUBLIC_BASE_URL": "https://console.example.test",
        "CREDENTIALS_KEY": "base64:BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ=",
    }
    values.update(overrides)
    return Settings(_env_file=None, _secrets_dir=None, **values)  # type: ignore[call-arg]


def test_settings_defaults_keep_mail_off() -> None:
    settings = configured()
    assert settings.INVITE_MAIL_ENABLED is False
    assert settings.INVITE_MAIL_FROM == SENDER
    assert (settings.SMTP_HOST, settings.SMTP_PORT) == ("smtp-relay.gmail.com", 587)
    assert settings.SMTP_USERNAME is None and settings.SMTP_PASSWORD is None


@pytest.mark.parametrize(
    ("overrides", "active"),
    [
        ({}, False),
        ({"SMTP_USERNAME": "relay@example.test", "SMTP_PASSWORD": "synthetic-pw"}, False),
        ({"INVITE_MAIL_ENABLED": True}, False),
        ({"INVITE_MAIL_ENABLED": True, "SMTP_USERNAME": "relay@example.test"}, False),
        ({"INVITE_MAIL_ENABLED": True, "SMTP_PASSWORD": "synthetic-pw"}, False),
        (
            {"INVITE_MAIL_ENABLED": True, "SMTP_USERNAME": "", "SMTP_PASSWORD": "synthetic-pw"},
            False,
        ),
        (
            {
                "INVITE_MAIL_ENABLED": True,
                "SMTP_USERNAME": "relay@example.test",
                "SMTP_PASSWORD": "synthetic-pw",
            },
            True,
        ),
    ],
)
def test_from_settings_requires_flag_and_both_secrets(
    overrides: dict[str, Any], active: bool
) -> None:
    mailer = Mailer.from_settings(configured(**overrides))
    assert mailer.enabled is active
    assert mailer.sender == SENDER
    if active:
        transport = mailer.transport
        assert isinstance(transport, SmtpTransport)
        assert (transport.host, transport.port, transport.timeout) == (
            "smtp-relay.gmail.com",
            587,
            10,
        )
        assert "synthetic-pw" not in repr(transport)


def test_smtp_transport_requires_starttls_before_login(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    class FakeSmtp:
        def __init__(self, host: str, port: int, *, timeout: float) -> None:
            calls.append(("connect", host, port, timeout))

        def __enter__(self) -> FakeSmtp:
            return self

        def __exit__(self, *_: object) -> None:
            calls.append(("quit",))

        def starttls(self, *, context: Any) -> None:
            calls.append(("starttls", context.verify_mode))

        def login(self, username: str, password: str) -> None:
            calls.append(("login", username, password))

        def send_message(self, message: Any) -> None:
            calls.append(("send", message["From"], message["To"], message["Subject"]))
            calls.append(("parts", [part.get_content_type() for part in message.iter_parts()]))

    monkeypatch.setattr("csa.console.mailer.smtplib.SMTP", FakeSmtp)
    SmtpTransport("relay.example.test", 587, "relay@example.test", "synthetic-pw").send(
        MAIL, SENDER
    )
    import ssl

    assert calls == [
        ("connect", "relay.example.test", 587, 10),
        ("starttls", ssl.CERT_REQUIRED),
        ("login", "relay@example.test", "synthetic-pw"),
        ("send", SENDER, "aminah@example.test", "Synthetic subject"),
        ("parts", ["text/plain", "text/html"]),
        ("quit",),
    ]
