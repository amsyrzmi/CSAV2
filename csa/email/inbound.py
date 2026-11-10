"""The inbound injection check before a reply is drafted: Jev's call, never the LLM's.

A customer's email can try to steer the assistant that drafts the reply. The drafter already
treats the email as untrusted data and that stays in place whatever this check says. This check
adds a refusal: when Jev says, at or above its confidence floor, that the latest inbound body
tries to change, reveal or override the assistant's instructions (or pretends to be the system
or staff), the reply is not drafted. The fallback is the constant ``False``: no model ever gets
to overrule a Jev "yes", and a Jev error, timeout or low confidence never blocks anything.

Only the latest inbound body goes to the decider (which redacts it, and the sender's display name
when known); never the thread history, the subject or the staff's instructions.
"""

from __future__ import annotations

from email.utils import parseaddr
from typing import Any
from uuid import UUID

from csa.decide.deciders import Decider
from csa.decide.jev import INJECTION_QUESTION

QUESTION = "injection"


async def _never_a_block() -> dict[str, Any]:
    return {QUESTION: False}


def sender_display_name(sender: str | None) -> str | None:
    """The display name of a ``From`` value (``Name <address>``), or None when it has none."""
    if not sender:
        return None
    name = parseaddr(sender)[0].strip()
    return name or None


async def inbound_injection(
    decider: Decider | None,
    body: str,
    *,
    sender_name: str | None,
    trace_id: UUID,
    client_id: UUID,
) -> bool:
    """True when Jev, at or above its floor, says the inbound email tries to steer the assistant."""
    if decider is None:
        return False
    names = [sender_name.strip()] if sender_name and sender_name.strip() else []
    decided = await decider.decide(
        {"message": body},
        {QUESTION: INJECTION_QUESTION},
        fallback=_never_a_block,
        trace_id=trace_id,
        client_id=client_id,
        names=names,
    )
    return decided.answers[QUESTION] is True
