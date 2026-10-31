"""Pure workspace turn rules, shared by the turn service and the model smoke run.

Nothing here reads a database, a request or a model. The turn service decides which parts of a
message the user wrote (``_authored`` in ``csa/workspace/service.py``, security-critical) and
passes only those parts in; these rules then say what the words mean: an email request, a
send, a question for content the planner writes itself, a placeholder option, a tab the user
can open. The correction notes and reason codes for a re-planned turn live here too.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from csa.console.contracts import Role
from csa.console.services import PackageState
from csa.email.models import is_employee_ref
from csa.workspace.contracts import ClarificationOption, ClarificationProposal, WorkspaceProposal

ADDRESS = re.compile(r"[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# --- The send rule's words -------------------------------------------------------------------
SEND_WORDS = re.compile(r"\b(send|hantar|hantarkan|kirim|kirimkan)\b", re.IGNORECASE)
DRAFT_WORDS = re.compile(r"\b(drafts?|draf|save|simpan)\b", re.IGNORECASE)
NEGATED_SEND = re.compile(
    r"\b(?:don['’]?t|do not|never|jangan)\s+(?:send|hantar|kirim)\w*", re.IGNORECASE
)
EMAIL_VERB = re.compile(r"\b(e-?mails?|e-?mel|emel)\b", re.IGNORECASE)
WRITE_WORD = re.compile(r"\b(write|tulis|karang)\b", re.IGNORECASE)


def asks_to_send(text: str, plan_send: bool) -> bool:
    """A send card for these user-written words; the first rule that matches wins.

    1. A draft word ("draft", "drafts", "save", "simpan") or a negated send ("don't send",
       "do not send", "never send", "jangan hantar") gives a draft card, even next to "send".
    2. A send word ("send", "hantar", "kirim") gives a send card, unless a write word ("write",
       "tulis", "karang") is there too and the plan's ``send`` is false.
    3. The plan's ``send`` with an email verb ("email", "e-mail", "e-mel") and no write word
       gives a send card.
    4. Anything else gives a draft card.
    """
    if DRAFT_WORDS.search(text) or NEGATED_SEND.search(text):
        return False
    written = bool(WRITE_WORD.search(text))
    if SEND_WORDS.search(text) and (plan_send or not written):
        return True
    return bool(plan_send and EMAIL_VERB.search(text) and not written)


# --- Placeholder options ---------------------------------------------------------------------
# An option that only asks the user to type ("Enter address", "Masukkan alamat") or a code the
# model made up for one (enter_address, subject_body): clicking it would send the code back.
PLACEHOLDER_LABEL = re.compile(
    r"(?:enter|type|provide|input|write|specify|fill in|masukkan|taip|berikan|isi)\b",
    re.IGNORECASE,
)
PLACEHOLDER_VALUE = re.compile(r"[a-z]+(?:_[a-z]+)+")
# A snake_case value is a placeholder only when a token says what to type; real snake_case
# choices (knowledge_base, first_aid, training_request) stay.
PLACEHOLDER_TOKENS = frozenset(
    {
        "enter", "type", "provide", "input", "specify", "write", "address", "subject", "body",
        "email", "details", "message", "content",
    }
)  # fmt: skip


def is_placeholder(option: ClarificationOption) -> bool:
    """The option asks the user to type rather than offering a choice to click.

    A label that starts with a whole word such as "Enter" or "Masukkan", or a snake_case
    value with a token such as "enter", "address" or "subject" (enter_address, subject_body).
    Real choices survive: "Enterprise plan", "First aid", knowledge_base, an employee id or a
    thread id.
    """
    if PLACEHOLDER_LABEL.match(option.label.strip()):
        return True
    value = option.value.strip()
    tokens = value.split("_")
    return bool(PLACEHOLDER_VALUE.fullmatch(value)) and not PLACEHOLDER_TOKENS.isdisjoint(tokens)


# --- The content check -----------------------------------------------------------------------
# A clarification that asks for what the planner must write itself; corrected on email turns.
CONTENT_WORDS = re.compile(r"\b(subject|body|wording|content|tajuk|isi|kandungan)\b", re.IGNORECASE)
# Email words only: "send" and "hantar" also send a training request, so they do not count.
EMAIL_REQUEST = re.compile(r"\b(e-?mails?|e-?mel|emel|reply|balas|mail)\b", re.IGNORECASE)


def asks_for_content(proposal: WorkspaceProposal) -> bool:
    """A clarification asking for the subject or wording, which the planner writes itself."""
    if not isinstance(proposal, ClarificationProposal):
        return False
    return bool(CONTENT_WORDS.search(proposal.question))


def about_email(parts: Iterable[str]) -> bool:
    """These user-written parts are about email.

    An email word ("email", "e-mel", "reply", "balas", "mail") or an address. A bare send word
    ("send", "hantar") is not email: it also sends a training request.
    """
    return any(EMAIL_REQUEST.search(part) or ADDRESS.search(part) for part in parts)


def picks_thread_or_colleague(proposal: WorkspaceProposal, threads: Iterable[str]) -> bool:
    """A clarification offering a synced thread (one of ``threads``) or a colleague.

    Such a question may name a subject ("Which email, 'Invoice March' or 'Invoice April'?")
    without asking the user to write one. The raw option values are checked: a thread id or
    an employee id.
    """
    if not isinstance(proposal, ClarificationProposal):
        return False
    known = set(threads)
    values = [option.value.strip() for option in proposal.options]
    return any(value in known or is_employee_ref(value) for value in values)


# --- One correction per turn -----------------------------------------------------------------
# Fixed server text for a re-plan: never user or mail text.
CONTENT_NOTE = (
    "Do not ask for the subject or the wording. Write them yourself from the request; if the "
    "request says nothing about the content, write a short, neutral email from what it does say."
)
# Reasons the server can name when it refuses a plan (PlannerInvalidOutput.reason).
RECIPIENT_NOT_REQUESTED = "recipient_not_requested"
UNKNOWN_THREAD = "unknown_thread"
BLANK_QUERY = "blank_query"
OPTION_TOO_LONG = "option_too_long"
CONTRACT_INVALID = "contract_invalid"
CORRECTION_NOTES: dict[str, str] = {
    RECIPIENT_NOT_REQUESTED: "Use only recipients, cc and bcc the user asked for: an address "
    "written in the message, or an employee id from the message, history or my_manager; if "
    "there is none, ask the user to type the email address.",
    UNKNOWN_THREAD: "For email_reply use only a thread_id listed in mailbox_threads; if no "
    "thread matches, answer that the conversation was not found in the synced email.",
    BLANK_QUERY: "A directory_lookup needs a person's name or job title from the message; if "
    "the message names nobody, ask who the user means.",
    OPTION_TOO_LONG: "Keep every clarification option short, with a label under 100 "
    "characters, a value under 200 characters and no email addresses.",
    CONTRACT_INVALID: "Return one JSON object that matches the schema exactly; for "
    "email_compose set exactly one of recipient_employee_id and recipient_address.",
}


# --- Tabs the user can open (mirrors navigationFor in web/lib/navigation.ts) ------------------
ACTIVE = frozenset({"active"})
# Tab -> (packages that unlock it, accepted statuses).
TAB_GATES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "inbox": (frozenset({"customer_support"}), frozenset({"active", "suspended"})),
    "knowledge_base": (frozenset({"customer_support"}), ACTIVE),
    "business": (frozenset({"orders_products", "marketplaces"}), ACTIVE),
    "hr": (frozenset({"hr"}), ACTIVE),
    "email": (frozenset({"staff_email"}), ACTIVE),
    "subscriptions": (frozenset({"subscriptions"}), ACTIVE),
}
# Tabs the console shows only to members who can write (not read_only).
WRITER_TABS = frozenset({"inbox", "email", "business"})
Gate = tuple[frozenset[str], frozenset[str]]


def unlocked(packages: Iterable[PackageState], gate: Gate) -> bool:
    """One of the gate's packages is in one of its accepted statuses."""
    keys, statuses = gate
    return any(p.key in keys and p.status in statuses for p in packages)


def role_allows_tab(tab: str, role: Role) -> bool:
    """The role part of the console rule: read-only members never see the writer tabs."""
    return not (tab in WRITER_TABS and role == Role.READ_ONLY)


def tab_available(tab: str, role: Role, packages: list[PackageState]) -> bool:
    """The console offers this tab to this role with these package states.

    The package and role rule of ``navigationFor``: a gated tab needs one of its packages in
    an accepted status, except for platform admins, and the writer tabs are never offered to
    read-only members. HR-access gating stays in the console.
    """
    if not role_allows_tab(tab, role):
        return False
    gate = TAB_GATES.get(tab)
    return role == Role.PLATFORM_ADMIN or gate is None or unlocked(packages, gate)
