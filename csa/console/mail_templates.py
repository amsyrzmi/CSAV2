"""Invitation and reset email copy: plain text plus a minimal HTML part.

Copy follows docs/WRITING_GUIDE.md. Every value is escaped in the HTML part, and the
subject is kept to one line so a business name can never add a header.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from csa.console.mailer import OutgoingMail

# Password resets have no tenant, so their times use the console's own zone.
RESET_TIMEZONE = "Asia/Kuala_Lumpur"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_BODY = (
    "margin:0;padding:24px;font-family:Arial,Helvetica,sans-serif;font-size:15px;"
    "line-height:1.5;color:#1f2933;"
)
_BUTTON = (
    "display:inline-block;padding:10px 18px;background:#1f2933;color:#ffffff;"
    "text-decoration:none;border-radius:6px;"
)


def when(moment: datetime, tz: str) -> str:
    """Format as in the writing guide, for example ``27 Oct 2026, 09:15``."""
    local = moment.astimezone(ZoneInfo(tz))
    return f"{local.day} {_MONTHS[local.month - 1]} {local.year}, {local:%H:%M}"


def _one_line(value: str) -> str:
    return " ".join(value.split())


def _html(paragraphs: list[str]) -> str:
    body = "".join(f"<p>{paragraph}</p>\n" for paragraph in paragraphs)
    return f'<!DOCTYPE html>\n<html lang="en">\n<body style="{_BODY}">\n{body}</body>\n</html>\n'


def _link_paragraphs(link: str, action: str) -> list[str]:
    href = escape(link)
    return [
        f'<a href="{href}" style="{_BUTTON}">{action}</a>',
        f"If the button does not work, copy this link into your browser:<br>{href}",
    ]


def invitation_mail(
    *,
    to: str,
    invitee_name: str,
    inviter_name: str,
    business_name: str,
    link: str,
    expires_at: datetime,
    tz: str,
) -> OutgoingMail:
    expiry = when(expires_at, tz)
    joined = f"{inviter_name} has invited you to join {business_name} on FourStacks."
    expires = f"The link expires on {expiry}. After that, ask {inviter_name} for a new invitation."
    unexpected = (
        "If you did not expect this invitation, you can ignore this email. "
        "Nothing changes until you open the link and set a password."
    )
    text = (
        f"Hello {invitee_name},\n\n{joined}\n\n"
        f"Open this link to set your password and sign in:\n{link}\n\n"
        f"{expires}\n\n{unexpected}\n\nFourStacks\n"
    )
    html = _html(
        [
            f"Hello {escape(invitee_name)},",
            f"{escape(inviter_name)} has invited you to join "
            f"<strong>{escape(business_name)}</strong> on FourStacks.",
            *_link_paragraphs(link, "Accept invitation"),
            escape(expires),
            escape(unexpected),
            "FourStacks",
        ]
    )
    subject = f"You're invited to {_one_line(business_name)} on FourStacks"
    return OutgoingMail(to=to, subject=subject, text=text, html=html)


def reset_mail(*, to: str, name: str, link: str, expires_at: datetime) -> OutgoingMail:
    started = "A FourStacks administrator has started a password reset for your account."
    expires = f"The link expires on {when(expires_at, RESET_TIMEZONE)}."
    unexpected = (
        "If you did not expect this email, you can ignore it. "
        "Your password stays the same until you choose a new one."
    )
    text = (
        f"Hello {name},\n\n{started}\n\n"
        f"Open this link to choose a new password:\n{link}\n\n"
        f"{expires}\n\n{unexpected}\n\nFourStacks\n"
    )
    html = _html(
        [
            f"Hello {escape(name)},",
            started,
            *_link_paragraphs(link, "Choose a new password"),
            expires,
            unexpected,
            "FourStacks",
        ]
    )
    return OutgoingMail(to=to, subject="Reset your FourStacks password", text=text, html=html)
