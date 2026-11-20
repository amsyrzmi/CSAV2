"""Workspace planner: one model call turns a staff message into one typed proposal.

The planner only proposes. Every gate (role, package, switch, record version) is enforced by
the turn service and the proposal registry afterwards, so a wrong plan can at worst produce a
refused turn or a confirmation card the user declines. The prompt carries server-built facts
only: role, package states, the page the user is on, a redacted record summary, the thread's
last stored (redacted) turns with the ids they offered, and the caller's own manager id when
asked — never directory listings or record bodies. Email turns add the caller's own direct
reports (ids and names, for "email my team") and up to ten recent synced threads, which are
untrusted: an address seen only there is never accepted as a recipient (see the turn service).
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import cache
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import Field, ValidationError

from csa.console.contracts import Role
from csa.console.services import PackageState
from csa.decide.completion import CompletionError, JsonCompletionClient
from csa.workspace.contracts import (
    AnswerProposal,
    BookingActionProposal,
    ClarificationProposal,
    DirectoryLookupProposal,
    EmailBatchProposal,
    EmailComposeProposal,
    EmailReplyProposal,
    EmailTeamProposal,
    HrPersonAddProposal,
    HrPersonLeaveProposal,
    HrPersonOpenProposal,
    HrPersonUpdateProposal,
    HrProcessReadProposal,
    HrTaskCompleteProposal,
    InboxDecisionProposal,
    InboxSummaryProposal,
    Language,
    NavigateProposal,
    PageContext,
    PeopleQueryProposal,
    PlanProposal,
    ProductStockReadProposal,
    TeamReason,
    TrainingAskProposal,
    TrainingSubmitProposal,
    WorkspaceProposal,
    _Contract,
)
from csa.workspace.prompt_areas import Area, build_prompt
from csa.workspace.rules import CONTRACT_INVALID

PlannerProposal = Annotated[
    Union[  # noqa: UP007 - Annotated discriminated union of named members
        AnswerProposal,
        NavigateProposal,
        DirectoryLookupProposal,
        EmailComposeProposal,
        EmailReplyProposal,
        EmailTeamProposal,
        InboxSummaryProposal,
        TrainingSubmitProposal,
        InboxDecisionProposal,
        ClarificationProposal,
        HrPersonAddProposal,
        HrPersonUpdateProposal,
        HrPersonLeaveProposal,
        PeopleQueryProposal,
        HrPersonOpenProposal,
        HrProcessReadProposal,
        HrTaskCompleteProposal,
        BookingActionProposal,
        EmailBatchProposal,
        PlanProposal,
        ProductStockReadProposal,
        TrainingAskProposal,
    ],
    Field(discriminator="action"),
]

# Fixed server text about the page the user is on, by "<tab>_<section>" of the page context.
PAGE_HINTS: dict[str, str] = {
    "business_orders": "Business management, Orders: the tenant's orders, including orders "
    "copied from a connected TikTok Shop. An open order is in record_summary. The assistant "
    "only reads them; it cannot ship, cancel or refund an order.",
    "business_products": "Business management, Products & stock: the tenant's own catalogue "
    "and the products copied from a connected TikTok Shop. An open TikTok Shop product is in "
    'record_summary; for its stock ("how many do we have") use product_stock_read.',
    "business_stores": "Business management, Store connections: an Admin connects or "
    "disconnects a TikTok Shop on this page; the assistant cannot do it.",
    "training_library_item": "Training library, one item: a training video or PDF the company "
    "added for its staff. An open video is in record_summary; for a question about what it says "
    "use training_ask and leave item out.",
}


def page_hint(context: PageContext | None) -> str | None:
    """The fixed hint for the page in the context, or None."""
    if context is None or context.section is None:
        return None
    return PAGE_HINTS.get(f"{context.tab}_{context.section}")


class PriorTurn(_Contract):
    """One earlier turn of this thread: its stored (already redacted) message and outcome.

    Only identifiers the user was shown travel here (directory candidate ids, clarification
    option values); never names, addresses or record bodies. ``resume_offset`` is where "the
    rest" of a batch email's group starts: a count, never who.
    """

    message: str
    action: str
    result: str
    proposal_kind: str | None = None
    offered_employee_ids: list[UUID] = Field(default_factory=list)
    question: str | None = None
    offered_options: list[str] = Field(default_factory=list)
    chosen: str | None = None
    resume_offset: int | None = Field(default=None, ge=1)
    # The group and days that count was taken on, for the server's check only (never shown to
    # the planner): an enum and a number.
    resume_group: Literal["active_subscribers", "renewing_soon"] | None = Field(
        default=None, exclude=True
    )
    resume_days: int | None = Field(default=None, ge=1, le=365, exclude=True)


class ManagerFact(_Contract):
    """The caller's own manager, resolved before planning when the message mentions one."""

    employee_id: UUID | None
    reason: Literal["no_employee_link", "no_manager", "package_inactive"] | None


class MailboxThreadFact(_Contract):
    """One recent synced thread of the caller's own mailbox. UNTRUSTED: any sender wrote it."""

    thread_id: str
    sender_name: str | None
    sender_address: str | None
    subject: str = Field(max_length=120)
    received_at: datetime | None
    priority: Literal["low", "normal", "high"] | None


class TeamMemberFact(_Contract):
    employee_id: UUID
    name: str


class TeamFact(_Contract):
    """The caller's own active direct reports, only for holders of the approves-team switch.

    ``skipped`` have no work email and are left out of a team email. With a ``reason`` both
    lists are empty.
    """

    reports: list[TeamMemberFact]
    skipped: list[TeamMemberFact]
    reason: TeamReason | None


class EmployeeFact(_Contract):
    """The caller's own readiness to request training, resolved when the message is about it."""

    linked: bool
    can_request_training: bool
    reason: Literal["hr_inactive", "no_hr_access", "no_employee_link", "assistant_off"] | None


class BookingFacts(_Contract):
    """What the model needs to turn "tomorrow at 3" into a date and a time: the company's own
    clock. Present only when the Bookings tab is open to this company and the message is about
    bookings; ``can_change`` is false for a read-only member, who may ask for free times only."""

    now_local: str = Field(max_length=16)  # "2026-11-05 14:03" in the company's time zone
    weekday: str = Field(max_length=12)
    timezone: str = Field(max_length=64)
    can_change: bool


class PlannerFacts(_Contract):
    role: Role
    packages: list[PackageState]
    page_context: PageContext | None
    record_summary: str | None
    language: Language
    history: list[PriorTurn] = Field(default_factory=list, max_length=6)
    my_manager: ManagerFact | None = None
    mailbox_threads: list[MailboxThreadFact] | None = Field(default=None, max_length=10)
    my_team: TeamFact | None = None
    my_employee: EmployeeFact | None = None
    # The caller's People view (hr_people_viewer) when the message is about people records.
    people_access: Literal["hr", "directory", "self"] | None = None
    # The company's clock, when the message is about bookings and the Bookings tab is open.
    bookings: BookingFacts | None = None
    # Fixed server text about the page (PAGE_HINTS).
    page_hint: str | None = None
    # The areas the scoped prompt covers, for the server's own use (the prompt choice, which facts
    # travel, the widening check): never shown to the model. None is the full prompt.
    areas: frozenset[Area] | None = Field(default=None, exclude=True)


class PlannerOutput(_Contract):
    """What the model must return; legacy guidance/order actions are not plannable."""

    proposal: PlannerProposal


class PlannerError(Exception):
    """Planner failure with a stable, content-free code and HTTP status."""

    status_code = 502

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PlannerInvalidOutput(PlannerError):
    """The plan broke the contract or a server rule; ``reason`` names the rule when the server
    can (see ``csa/workspace/rules.py``), so the turn can be re-planned once. The code and status
    stay the same for every reason."""

    def __init__(self, reason: str | None = None) -> None:
        super().__init__("planner_invalid_output")
        self.reason = reason


class PlannerUnavailable(PlannerError):
    """The model provider failed (timeout, transport or HTTP error); retryable."""

    status_code = 503

    def __init__(self) -> None:
        super().__init__("planner_unavailable")


_MALAY_MARKERS = frozenset(
    {
        "saya", "awak", "anda", "kamu", "boleh", "tolong", "sila", "nak", "hendak", "mahu",
        "untuk", "dengan", "dan", "yang", "ini", "itu", "apa", "siapa", "bila", "mana",
        "bagaimana", "kenapa", "mengapa", "tak", "tidak", "bukan", "kepada", "daripada",
        "pengurus", "latihan", "emel", "hantar", "mohon", "permohonan", "terima", "kasih",
        "buka", "cari", "bantu", "ada", "sudah", "belum", "akan", "dia", "kami", "kita",
        "kat", "pasal", "esok", "bos", "tentang", "mesyuarat", "cuti", "nanti", "buat",
        "hari", "minggu", "bulan", "semua", "berapa", "banyak", "baca", "ya", "tugas", "tugasan",
        "selesai", "tertunggak", "lewat", "orientasi", "proses",
    }
)  # fmt: skip
_ENGLISH_MARKERS = frozenset(
    {
        "to", "the", "an", "my", "me", "please", "can", "you", "for", "about", "open",
        "send", "what", "how", "show", "find", "need", "and", "of", "is", "are", "in", "on",
        "with", "who", "when", "where", "why", "do", "does", "it", "this", "that",
        "want", "would", "could", "have", "from", "them", "their", "yes", "email", "emails",
        "as", "add", "she", "he", "her", "his",
    }
)  # fmt: skip
# Words both languages use in a console message; they say nothing about the language.
_SHARED_WORDS = frozenset({"hr", "gmail", "tab", "ok", "okay"})
_WORD = re.compile(r"[a-zA-Z]+")
_ADDRESS_OR_URL = re.compile(r"\S+@\S+|(?:https?://|www\.)\S+", re.IGNORECASE)


def detect_language(text: str) -> Language | None:
    """Guess "en" or "ms" by function words; None when the message is not clear.

    Addresses, URLs, digits, one-letter words and the words both languages share are ignored.
    A side wins when it has at least one marker and strictly more than the other.
    """
    words = [w.lower() for w in _WORD.findall(_ADDRESS_OR_URL.sub(" ", text))]
    words = [w for w in words if len(w) >= 2 and w not in _SHARED_WORDS]
    malay = sum(1 for w in words if w in _MALAY_MARKERS)
    english = sum(1 for w in words if w in _ENGLISH_MARKERS)
    if malay > english:
        return "ms"
    if english > malay:
        return "en"
    return None


SYSTEM_PROMPT = build_prompt(PlannerOutput.model_json_schema(), None)


@cache
def scoped_prompt(areas: frozenset[Area]) -> str:
    """The system prompt for a turn that selected ``areas``: only their actions and bullets."""
    return build_prompt(PlannerOutput.model_json_schema(), areas)


# The tags that wrap the message and the server's note; the message may not open or close one.
_MESSAGE_TAG = re.compile(r"<(?=/?(?:message|correction))", re.IGNORECASE)


# The facts whose payload belongs to areas: it travels only when one of them is selected.
# Everything else (history, the record summary, the page hint, role and packages) is general.
_FACT_AREAS: dict[str, frozenset[Area]] = {
    "my_manager": frozenset({"email", "hr"}),
    "mailbox_threads": frozenset({"email"}),
    "my_team": frozenset({"email"}),
    "my_employee": frozenset({"training"}),
    "people_access": frozenset({"hr"}),
    "bookings": frozenset({"bookings"}),
}


def _scoped_facts(facts: PlannerFacts) -> PlannerFacts:
    """``facts`` without the payloads of areas the scoped prompt does not cover."""
    if facts.areas is None:
        return facts
    chosen = facts.areas
    return facts.model_copy(
        update={name: None for name, owners in _FACT_AREAS.items() if not owners & chosen}
    )


def planner_prompt(
    message: str, facts: PlannerFacts, note: str | None = None, *, widen: bool = False
) -> str:
    """The user prompt; ``note`` is fixed server text (never user or mail text) for a re-plan.

    A scoped turn (``facts.areas`` set) drops the facts of other areas; a ``widen`` call, which
    uses the full prompt, keeps every fact that was loaded.
    """
    if not widen:
        facts = _scoped_facts(facts)
    # JSON-escape < and > so untrusted text (mail subjects, history) cannot close the tags.
    data = facts.model_dump_json().replace("<", "\\u003c").replace(">", "\\u003e")
    # The same escape for a <message> or <correction> tag in the message, so it cannot end the
    # message early or forge a server correction.
    message = _MESSAGE_TAG.sub(lambda _: "\\u003c", message)
    prompt = f"<facts>{data}</facts>\n<message>\n{message}\n</message>"
    if note is not None:
        prompt += f"\n<correction>\n{note}\n</correction>"
    return prompt


def _parse_failure(exc: CompletionError) -> str | None:
    """``contract_invalid`` when the reply was JSON that broke PlannerOutput (a validator, a
    missing field, a legacy action); a reply that is not JSON at all names no rule."""
    cause = exc.__cause__
    if isinstance(cause, ValidationError) and all(
        error["type"] != "json_invalid" for error in cause.errors()
    ):
        return CONTRACT_INVALID
    return None


class WorkspacePlanner:
    def __init__(self, client: JsonCompletionClient, *, timeout: float = 20.0) -> None:
        self._client = client
        self._timeout = timeout

    async def plan(
        self,
        message: str,
        facts: PlannerFacts,
        *,
        note: str | None = None,
        widen: bool = False,
    ) -> WorkspaceProposal:
        """One model call. ``facts.areas`` scopes the prompt; ``widen`` forces the full one."""
        system = SYSTEM_PROMPT if widen or facts.areas is None else scoped_prompt(facts.areas)
        try:
            output = await self._client.json_completion(
                PlannerOutput,
                system=system,
                user=planner_prompt(message, facts, note, widen=widen),
                timeout=self._timeout,
            )
        except CompletionError as exc:
            if exc.code == "groq_invalid_output":
                raise PlannerInvalidOutput(_parse_failure(exc)) from None
            raise PlannerUnavailable() from None
        if not isinstance(output, PlannerOutput):
            raise PlannerInvalidOutput()
        return output.proposal
