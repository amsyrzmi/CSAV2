"""Owner-triggered model smoke run for the workspace assistant (never CI).

Runs the real planner (``WorkspacePlanner.plan``) and the real email-drafting call
(``EmailService._compose_with_model``) on the synthetic cases in
``csa/workspace/smoke_cases.json``. Each case goes through the turn service's own correction
loop (``_corrected_plan``: at most two planner calls) with a resolve step that runs every check
the turn path can make without a connection: the recipient check (``_check_recipients``, through
``_named_recipient_check``), the reply thread, a blank directory query, clarification option
limits (``_clarification``) and the tab rule (``rules.tab_available``). Then it checks the
placeholder options, the send rule (``_asked_to_send``), the expected recipient and the draft.
No database, Gmail, tenant data, writes or HTTP routes are involved; the facts come from the
synthetic presets below.

It runs inside the staging gateway container, which has the model key mounted::

    docker exec csa-staging-gateway-1 python -m scripts.assistant_smoke [--repeat N] [--case ID]

Output is content-free: one ``PASS|FAIL <id> action=<action>`` line per sample, the failing
field names after a failure, ``RATE <id> k/N`` per case with ``--repeat``, and a last
``N passed, M failed`` line. Never a message, model text, subject, body or address; the key is
never printed. Exit 0 when every sample passes, 1 when any fails, 2 on a setup error (no model
key, the model unavailable on the first call, an unknown case id, a missing or invalid case
file). A run is about 46 model calls (one planner call per case plus one drafting call per draft
case), up to about 81 with re-plans.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4, uuid5

import httpx
import structlog
from csa.console.contracts import Role
from csa.console.services import PackageState
from csa.core.settings import get_settings
from csa.decide.completion import CompletionError, JsonCompletionClient
from csa.decide.groq import GroqClient
from csa.email.models import validated_draft
from csa.email.service import EmailService
from csa.workspace.contracts import (
    ClarificationProposal,
    DirectoryLookupProposal,
    EmailComposeProposal,
    EmailReplyProposal,
    EmailTeamProposal,
    NavigateProposal,
    PageContext,
    TabKey,
    WorkspaceProposal,
)
from csa.workspace.planner import (
    EmployeeFact,
    MailboxThreadFact,
    ManagerFact,
    PlannerFacts,
    PlannerInvalidOutput,
    PlannerUnavailable,
    TeamFact,
    TeamMemberFact,
    WorkspacePlanner,
)
from csa.workspace.rules import (
    BLANK_QUERY,
    CONTRACT_INVALID,
    OPTION_TOO_LONG,
    RECIPIENT_NOT_REQUESTED,
    UNKNOWN_THREAD,
    is_placeholder,
    tab_available,
)
from csa.workspace.service import (
    _MAIL_WORDS,
    _MANAGER_WORDS,
    _TEAM_WORDS,
    _TRAINING_WORDS,
    _asked_to_send,
    _clarification,
    _corrected_plan,
    _named_recipient_check,
    _Planned,
)
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

CASES_FILE = Path(__file__).resolve().parent.parent / "csa" / "workspace" / "smoke_cases.json"
SESSION_IDLE_SECONDS = 900  # EmailService's guard; no session is ever read here
BRAND = {"brand_name": "Synthetic Trading Sdn Bhd"}
DRAFT_RECIPIENT = "colleague@example.com"  # synthetic; the server resolves real recipients
ActionName = Literal[
    "answer",
    "navigate",
    "directory_lookup",
    "email_compose",
    "email_reply",
    "email_team",
    "inbox_summary",
    "training_submit",
    "inbox_decision",
    "clarification",
]
Preset = Literal[
    "staff_basic",
    "staff_with_hr_linked",
    "staff_unlinked",
    "staff_with_mailbox",
    "staff_hr_inactive",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CaseFacts(_Strict):
    """A preset's synthetic facts, with optional overrides of the role and the open tab."""

    preset: Preset
    role: Role | None = None
    page_tab: TabKey | None = None


class Expect(_Strict):
    """Allowed actions (a set, not one action), forbidden ones and the optional checks."""

    actions: Annotated[list[ActionName], Field(min_length=1)]
    not_actions: list[ActionName] = Field(default_factory=list)
    send: bool | None = None
    no_placeholder_options: bool = False
    draft_valid: bool = False
    # email_compose must go to exactly this address (it is in the message); never printed.
    recipient: str | None = None
    # True: a navigate must reach a tab this user cannot open, so the turn answers with the
    # locked text. False: a navigate to such a tab fails with ``locked``.
    locked: bool = False


class SmokeCase(_Strict):
    """One model-testable row of docs/ASSISTANT_RETEST.md; ``id`` is ``<feature>-<lang>-<n>``."""

    id: str = Field(pattern=r"^[a-z_]+-(en|ms)-\d+$")
    language: Literal["en", "ms"]
    message: str = Field(min_length=1, max_length=4000)
    facts: CaseFacts
    expect: Expect


def load_cases(path: Path = CASES_FILE) -> list[SmokeCase]:
    return TypeAdapter(list[SmokeCase]).validate_json(path.read_bytes())


# --- Synthetic facts -------------------------------------------------------------------------
_IDS = UUID("6f1d2a4e-0b7c-4e59-9a51-3c2f8d7e1b00")
MANAGER_ID = uuid5(_IDS, "manager")
MANAGER_NAME = "Siti Aminah"
REPORTS = [
    TeamMemberFact(employee_id=uuid5(_IDS, "report-1"), name="Hafiz Rahman"),
    TeamMemberFact(employee_id=uuid5(_IDS, "report-2"), name="Mei Ling Tan"),
]
SKIPPED = [TeamMemberFact(employee_id=uuid5(_IDS, "report-3"), name="Kumar Raj")]
THREADS = [
    MailboxThreadFact(
        thread_id="smoke-thread-invoice",
        sender_name="Aina Rahman",
        sender_address="aina.rahman@example.com",
        subject="Invoice INV-1042 for October",
        received_at=None,
        priority="high",
    ),
    MailboxThreadFact(
        thread_id="smoke-thread-meeting",
        sender_name="Razak Ali",
        sender_address="razak.ali@example.com",
        subject="Friday planning meeting",
        received_at=None,
        priority="normal",
    ),
    MailboxThreadFact(
        thread_id="smoke-thread-newsletter",
        sender_name="Supplier News",
        sender_address="news@example.com",
        subject="This month's offers",
        received_at=None,
        priority="low",
    ),
]
_PACKAGES = ("customer_support", "staff_email", "hr")


@dataclass(frozen=True)
class _Preset:
    hr_status: Literal["active", "suspended"]
    manager: ManagerFact
    threads: list[MailboxThreadFact] | None  # None: no mailbox connected (unreadable)
    team: TeamFact
    employee: EmployeeFact


_MANAGER = ManagerFact(employee_id=MANAGER_ID, reason=None)
_NOT_APPROVER = TeamFact(reports=[], skipped=[], reason="not_team_approver")
_LINKED = EmployeeFact(linked=True, can_request_training=True, reason=None)
PRESETS: dict[str, _Preset] = {
    "staff_basic": _Preset("active", _MANAGER, None, _NOT_APPROVER, _LINKED),
    "staff_with_hr_linked": _Preset(
        "active", _MANAGER, None, TeamFact(reports=REPORTS, skipped=SKIPPED, reason=None), _LINKED
    ),
    "staff_unlinked": _Preset(
        "active",
        ManagerFact(employee_id=None, reason="no_employee_link"),
        None,
        TeamFact(reports=[], skipped=[], reason="no_employee_link"),
        EmployeeFact(linked=False, can_request_training=False, reason="no_employee_link"),
    ),
    "staff_with_mailbox": _Preset("active", _MANAGER, THREADS, _NOT_APPROVER, _LINKED),
    "staff_hr_inactive": _Preset(
        "suspended",
        ManagerFact(employee_id=None, reason="package_inactive"),
        None,
        TeamFact(reports=[], skipped=[], reason="package_inactive"),
        EmployeeFact(linked=False, can_request_training=False, reason="hr_inactive"),
    ),
}


def build_facts(case: SmokeCase) -> PlannerFacts:
    """The facts the turn path would build for this message, from the case's preset.

    The turn path's own word triggers (``_MANAGER_WORDS`` and the rest in the turn service)
    decide which facts are in the prompt, as on a real turn."""
    preset = PRESETS[case.facts.preset]
    message = case.message
    packages = [
        PackageState(key=key, status=preset.hr_status if key == "hr" else "active")
        for key in _PACKAGES
    ]
    return PlannerFacts(
        role=case.facts.role or Role.TENANT_STAFF,
        packages=packages,
        page_context=PageContext(tab=case.facts.page_tab or "overview"),
        record_summary=None,
        language=case.language,
        my_manager=preset.manager if _MANAGER_WORDS.search(message) else None,
        mailbox_threads=preset.threads if _MAIL_WORDS.search(message) else None,
        my_team=preset.team if _TEAM_WORDS.search(message) else None,
        my_employee=preset.employee if _TRAINING_WORDS.search(message) else None,
    )


# --- One sample ------------------------------------------------------------------------------
# A refusal the server names (a second one ends in the M7 fallback) and its failing field.
REASON_FIELDS = {
    RECIPIENT_NOT_REQUESTED: "recipient",
    UNKNOWN_THREAD: "thread",
    BLANK_QUERY: "query",
    OPTION_TOO_LONG: "options",
    CONTRACT_INVALID: "contract",
}


class ModelUnavailable(Exception):
    """The provider failed on the run's first call: a setup problem, so the run stops."""


@dataclass
class Outcome:
    """One sample's chosen action and failing field names: never any model text."""

    action: str
    failed: list[str] = field(default_factory=list)
    replanned: bool = False

    def line(self, case_id: str) -> str:
        status = "FAIL" if self.failed else "PASS"
        parts = [status, case_id, f"action={self.action}", *self.failed]
        if self.replanned:
            parts.append("replanned")
        return " ".join(parts)


@dataclass
class _Call:
    """One planner call of a turn: what the model proposed and how the server took it."""

    proposal: WorkspaceProposal | None = None
    refusal: str | None = None
    locked: bool = False


class _RecordingPlanner(WorkspacePlanner):
    """The real planner, recording each call so the outcome can name what happened."""

    def __init__(self, client: JsonCompletionClient) -> None:
        super().__init__(client)
        self.calls: list[_Call] = []

    async def plan(
        self, message: str, facts: PlannerFacts, *, note: str | None = None
    ) -> WorkspaceProposal:
        call = _Call()
        self.calls.append(call)
        try:
            call.proposal = await super().plan(message, facts, note=note)
        except PlannerInvalidOutput as exc:
            call.refusal = exc.reason or CONTRACT_INVALID
            raise
        return call.proposal


def _resolver(
    planner: _RecordingPlanner, message: str, facts: PlannerFacts
) -> Callable[[WorkspaceProposal], Awaitable[_Planned]]:
    """The turn path's ``_resolve`` checks that need no connection, in the same order."""

    async def resolve(proposal: WorkspaceProposal) -> _Planned:
        call = planner.calls[-1]
        try:
            if isinstance(proposal, NavigateProposal):
                call.locked = not tab_available(proposal.tab, facts.role, facts.packages)
            elif isinstance(proposal, DirectoryLookupProposal):
                if not proposal.query.strip():
                    raise PlannerInvalidOutput(BLANK_QUERY)
            elif isinstance(proposal, ClarificationProposal):
                live, stored = _clarification(proposal)
                return _Planned(result=live, stored=stored)
            elif isinstance(proposal, EmailComposeProposal):
                _named_recipient_check(proposal, message, facts)
            elif isinstance(proposal, EmailReplyProposal):
                synced = {thread.thread_id for thread in facts.mailbox_threads or []}
                if proposal.thread_id not in synced:
                    raise PlannerInvalidOutput(UNKNOWN_THREAD)
                _named_recipient_check(proposal, message, facts)
        except PlannerInvalidOutput as exc:
            call.refusal = exc.reason or CONTRACT_INVALID
            raise
        return _Planned()

    return resolve


async def _draft_is_valid(
    email: EmailService, proposal: EmailComposeProposal | EmailTeamProposal
) -> bool:
    """The real drafting call, then the contract every saved draft passes."""
    team = isinstance(proposal, EmailTeamProposal)
    named = isinstance(proposal, EmailComposeProposal) and proposal.recipient_employee_id
    try:
        draft = await email._compose_with_model(
            proposal.subject,
            proposal.instructions or proposal.subject,
            proposal.language,
            MANAGER_NAME if named else None,
            BRAND,
            team=team,
        )
        validated_draft(uuid4(), "compose", DRAFT_RECIPIENT, draft.subject, draft.body)
    except (CompletionError, TimeoutError, ValidationError):
        return False
    return True


async def run_case(
    client: JsonCompletionClient, email: EmailService, case: SmokeCase, *, first: bool = False
) -> Outcome:
    """Plan one case through the turn's correction loop and check what the user would get.

    ``first``: the run's first call, where a provider failure stops the run (exit 2)."""
    facts = build_facts(case)
    planner = _RecordingPlanner(client)
    try:
        returned, _ = await _corrected_plan(
            planner, case.message, facts, _resolver(planner, case.message, facts)
        )
    except PlannerUnavailable:
        if first and len(planner.calls) == 1:
            raise ModelUnavailable() from None
        return Outcome("unavailable", ["model"], len(planner.calls) > 1)
    except PlannerInvalidOutput:  # a refusal with no correction note: a 502 on the turn path
        return Outcome("invalid_output", ["contract"], len(planner.calls) > 1)
    last = planner.calls[-1]
    proposal = last.proposal
    outcome = Outcome(proposal.action if proposal else "invalid_output", [], len(planner.calls) > 1)
    failed = outcome.failed
    if last.refusal is not None:  # refused twice: the user got the M7 fallback
        failed.append(REASON_FIELDS.get(last.refusal, "contract"))
    elif returned is not proposal:  # asked for the content twice: the fixed content question
        failed.append("content_question")
    if proposal is None:
        return outcome
    expect = case.expect
    if proposal.action not in expect.actions:
        failed.append("actions")
    if proposal.action in expect.not_actions:
        failed.append("not_actions")
    if isinstance(proposal, NavigateProposal) and last.locked != expect.locked:
        failed.append("locked")
    if isinstance(proposal, ClarificationProposal) and expect.no_placeholder_options:
        # The server drops these options; one offered at all means the prompt drifted.
        if any(is_placeholder(option) for option in proposal.options):
            failed.append("placeholder_options")
    email_plan = (EmailComposeProposal, EmailReplyProposal, EmailTeamProposal)
    if expect.send is not None and isinstance(proposal, email_plan):
        if _asked_to_send(proposal, case.message, facts) != expect.send:
            failed.append("send")
    if expect.recipient is not None and isinstance(proposal, EmailComposeProposal):
        address = (proposal.recipient_address or "").strip().lower()
        if address != expect.recipient.lower() and "recipient" not in failed:
            failed.append("recipient")
    if expect.draft_valid and not failed:
        if isinstance(proposal, EmailComposeProposal | EmailTeamProposal):
            if not await _draft_is_valid(email, proposal):
                failed.append("draft")
    return outcome


async def run(
    client: JsonCompletionClient, cases: Sequence[SmokeCase], repeat: int, out: Callable[[str], Any]
) -> int:
    """Every case ``repeat`` times; prints content-free lines and returns the exit code."""
    # Built without a pool: only the pure drafting call is used, never a database method.
    email = EmailService(
        None,  # type: ignore[arg-type]
        gmail=None,
        model=client,
        session_idle_seconds=SESSION_IDLE_SECONDS,
    )
    passed = failed = 0
    first = True
    for case in cases:
        wins = 0
        for _ in range(repeat):
            try:
                outcome = await run_case(client, email, case, first=first)
            except ModelUnavailable:
                return _setup_error("model unavailable on the first call; nothing was checked")
            first = False
            out(outcome.line(case.id))
            wins += not outcome.failed
        if repeat > 1:
            out(f"RATE {case.id} {wins}/{repeat}")
        passed += wins
        failed += repeat - wins
    out(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


def _model_key() -> tuple[str, str] | None:
    """``(key, model)`` from settings (``/run/secrets`` in the container), or None. Nothing
    about a failure is printed: a settings error can carry a value."""
    try:
        settings = get_settings()
    except ValidationError:
        return None
    key = settings.GROQ_API_KEY.get_secret_value()
    return (key, settings.GROQ_MODEL) if key else None


def _setup_error(text: str) -> int:
    print(f"setup error: {text}", file=sys.stderr)
    return 2


async def _run_with_groq(key: str, model: str, cases: Sequence[SmokeCase], repeat: int) -> int:
    async with httpx.AsyncClient(trust_env=False) as http:
        return await run(GroqClient(key, model, http), cases, repeat, print)


def main(argv: Sequence[str] | None = None, *, client: JsonCompletionClient | None = None) -> int:
    """The CLI; tests pass a scripted ``client``, the real run builds one from settings."""
    parser = argparse.ArgumentParser(prog="python -m scripts.assistant_smoke")
    parser.add_argument("--repeat", type=int, default=1, help="samples per case (pass rate)")
    parser.add_argument("--case", action="append", default=[], help="run only this case id")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        return _setup_error("--repeat must be at least 1")
    try:
        cases = load_cases(CASES_FILE)
    except (OSError, ValidationError):
        # Fixed text: a validation error would quote the file, an OSError its path.
        return _setup_error("the smoke cases file is missing or invalid")
    if args.case:
        known = {case.id for case in cases}
        if unknown := [case_id for case_id in args.case if case_id not in known]:
            return _setup_error(f"unknown case id {', '.join(unknown)}")
        cases = [case for case in cases if case.id in args.case]
    if client is not None:
        return asyncio.run(run(client, cases, args.repeat, print))
    found = _model_key()
    if found is None:
        return _setup_error("no model key configured (GROQ_API_KEY)")
    # The client's call log (model, schema, latency) goes to stderr, apart from the results.
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(sys.stderr))
    return asyncio.run(_run_with_groq(*found, cases, args.repeat))


if __name__ == "__main__":
    raise SystemExit(main())
