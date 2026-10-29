"""Invitation and reset mail through an SMTP relay, sent after the identity transaction commits.

A failed send is never an error for the caller: it is reported as one category and the
console falls back to showing the link. Logs carry that category only, never the address,
the link or the body.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import TYPE_CHECKING, Literal, Protocol

import structlog

if TYPE_CHECKING:
    from csa.core.settings import Settings

logger = structlog.get_logger(__name__)

MailFailure = Literal[
    "smtp_disabled", "smtp_auth_failed", "smtp_refused", "smtp_timeout", "smtp_error"
]
MailOutcome = Literal["sent"] | MailFailure


@dataclass(frozen=True)
class OutgoingMail:
    to: str
    subject: str
    text: str
    html: str


class Transport(Protocol):
    def send(self, mail: OutgoingMail, sender: str) -> None: ...


class SmtpTransport:
    """One connection per message: STARTTLS with certificate checks, then SMTP auth."""

    def __init__(
        self, host: str, port: int, username: str, password: str, timeout: float = 10
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._username = username
        self._password = password

    def __repr__(self) -> str:
        return f"SmtpTransport(host={self.host!r}, port={self.port!r})"

    def send(self, mail: OutgoingMail, sender: str) -> None:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = mail.to
        message["Subject"] = mail.subject
        message["Date"] = formatdate(usegmt=True)
        message["Message-ID"] = make_msgid(domain="fourstacks.my")
        message.set_content(mail.text)
        message.add_alternative(mail.html, subtype="html")
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            # starttls raises SMTPNotSupportedError when the relay does not offer it.
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self._username, self._password)
            smtp.send_message(message)


class Mailer:
    def __init__(self, transport: Transport | None, sender: str, *, limit: float = 15) -> None:
        self.transport = transport
        self.sender = sender
        # Bounds the whole send; the socket timeout bounds each SMTP operation inside it.
        self.limit = limit

    @property
    def enabled(self) -> bool:
        return self.transport is not None

    @classmethod
    def from_settings(cls, settings: Settings) -> Mailer:
        """Active only when the flag is on and both relay secrets are present and non-empty."""
        username, password = settings.SMTP_USERNAME, settings.SMTP_PASSWORD
        if (
            not settings.INVITE_MAIL_ENABLED
            or username is None
            or password is None
            or not username.get_secret_value()
            or not password.get_secret_value()
        ):
            return cls(None, settings.INVITE_MAIL_FROM)
        transport = SmtpTransport(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            username.get_secret_value(),
            password.get_secret_value(),
        )
        return cls(transport, settings.INVITE_MAIL_FROM)

    async def send(self, mail: OutgoingMail) -> MailOutcome:
        outcome = await self._attempt(mail)
        # Category only: the exception text can carry the recipient address.
        if outcome == "smtp_disabled":
            logger.info("console_mail_not_sent", category=outcome)
        elif outcome != "sent":
            logger.warning("console_mail_not_sent", category=outcome)
        return outcome

    async def _attempt(self, mail: OutgoingMail) -> MailOutcome:
        if self.transport is None:
            return "smtp_disabled"
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self.transport.send, mail, self.sender), self.limit
            )
        except smtplib.SMTPAuthenticationError:
            return "smtp_auth_failed"
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
            return "smtp_refused"
        except TimeoutError:
            return "smtp_timeout"
        except Exception:
            # Any other failure (connection, protocol, message building) still falls back to
            # the manual link; the invitation itself has already committed.
            return "smtp_error"
        return "sent"
