"""Owner-triggered model smoke run for the workspace assistant (never CI).

Runs the real planner (``WorkspacePlanner.plan``) and the real email-drafting call
(``EmailService._compose_with_model``) on the synthetic cases in
``csa/workspace/smoke_cases.json``, then checks each output with the same pure server rules the
turn path uses (``csa/workspace/rules.py``): the content check and its one re-plan, the
placeholder-option filter and the send rule. No database, Gmail, tenant data, writes or HTTP
routes are involved; the facts come from the synthetic presets below.

It runs inside the staging gateway container, which has the model key mounted::

    docker exec csa-staging-gateway-1 python -m scripts.assistant_smoke [--repeat N] [--case ID]

Output is content-free: one ``PASS|FAIL <id> action=<action>`` line per sample, the failing
field names after a failure, ``RATE <id> k/N`` per case with ``--repeat``, and a last
``N passed, M failed`` line. Never a message, model text, subject, body or address; the key is
never printed. Exit 0 when every sample passes, 1 when any fails, 2 on a setup error (no model
key, an unknown case id).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
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
    EmailComposeProposal,
    EmailReplyProposal,
    EmailTeamProposal,
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
    CONTENT_NOTE,
    CORRECTION_NOTES,
    about_email,
    asks_for_content,
    asks_to_send,
    is_placeholder,
    picks_thread_or_colleague,
)
from csa.workspace.service import _MAIL_WORDS, _MANAGER_WORDS, _TEAM_WORDS, _TRAINING_WORDS
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


def _corrects_content(proposal: WorkspaceProposal, case: SmokeCase, facts: PlannerFacts) -> bool:
    """The turn path's content check: on an email turn, a question for the subject or wording
    that does not pick a synced thread or a colleague. The message is all user-typed here."""
    threads = [thread.thread_id for thread in facts.mailbox_threads or []]
    return (
        asks_for_content(proposal)
        and about_email([case.message])
        and not picks_thread_or_colleague(proposal, threads)
    )


async def _plan(
    planner: WorkspacePlanner, case: SmokeCase, facts: PlannerFacts
) -> tuple[WorkspaceProposal | None, Outcome]:
    """At most two planner calls, as on the turn path: a content question or a contract break
    the planner can name is re-planned once with the fixed server note."""
    note: str | None = None
    for final in (False, True):
        try:
            proposal = await planner.plan(case.message, facts, note=note)
        except PlannerInvalidOutput as exc:
            if final or exc.reason not in CORRECTION_NOTES:
                return None, Outcome("invalid_output", ["contract"], note is not None)
            note = CORRECTION_NOTES[exc.reason]
            continue
        except PlannerUnavailable:
            return None, Outcome("unavailable", ["model"], note is not None)
        if _corrects_content(proposal, case, facts):
            if final:
                return proposal, Outcome(proposal.action, ["content_question"], True)
            note = CONTENT_NOTE
            continue
        return proposal, Outcome(proposal.action, [], note is not None)
    raise AssertionError("the second plan is always final")


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


async def run_case(planner: WorkspacePlanner, email: EmailService, case: SmokeCase) -> Outcome:
    """Plan one case and check the plan with the server's pure rules."""
    facts = build_facts(case)
    proposal, outcome = await _plan(planner, case, facts)
    if proposal is None:
        return outcome
    expect = case.expect
    failed = outcome.failed
    if proposal.action not in expect.actions:
        failed.append("actions")
    if proposal.action in expect.not_actions:
        failed.append("not_actions")
    if isinstance(proposal, ClarificationProposal) and expect.no_placeholder_options:
        # The server drops these options; one offered at all means the prompt drifted.
        if any(is_placeholder(option) for option in proposal.options):
            failed.append("placeholder_options")
    email_plan = (EmailComposeProposal, EmailReplyProposal, EmailTeamProposal)
    if expect.send is not None and isinstance(proposal, email_plan):
        if asks_to_send(case.message, proposal.send) != expect.send:
            failed.append("send")
    if expect.draft_valid and isinstance(proposal, EmailComposeProposal | EmailTeamProposal):
        if not await _draft_is_valid(email, proposal):
            failed.append("draft")
    return outcome


async def run(
    client: JsonCompletionClient, cases: Sequence[SmokeCase], repeat: int, out: Callable[[str], Any]
) -> int:
    """Every case ``repeat`` times; prints content-free lines and returns the exit code."""
    planner = WorkspacePlanner(client)
    # Built without a pool: only the pure drafting call is used, never a database method.
    email = EmailService(
        None,  # type: ignore[arg-type]
        gmail=None,
        model=client,
        session_idle_seconds=SESSION_IDLE_SECONDS,
    )
    passed = failed = 0
    for case in cases:
        wins = 0
        for _ in range(repeat):
            outcome = await run_case(planner, email, case)
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
    key = settings.GROQ_API_KEY.get_secret_value().strip()
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
    cases = load_cases()
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
