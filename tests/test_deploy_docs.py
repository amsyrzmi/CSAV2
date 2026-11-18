"""deploy/DEPLOY.md, the entry runbook: its headings, what it must name, and the moved sections.

Two older documents, ``deploy/PRODUCTION.md`` and ``deploy/STAGING_PIPELINE.md``, give whole
generic sections to DEPLOY.md and keep a heading and a pointer where something points at them.
``MOVED`` and ``KEEP_STUB`` hold exactly what must stay findable; they do not freeze every heading
of the old files, because feature rollout sections there are edited with each feature. That
nothing was dropped in a move was proved once, by a throwaway line-by-line check that is not part
of the repository.

Also here: the stale-wording guard (the image publish needs no approval today), which fails until
the older documents, the skills and the workflow header say so too, and the scan that keeps real
identifiers out of the public repository.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_DOC = ROOT / "deploy" / "DEPLOY.md"
PRODUCTION_DOC = ROOT / "deploy" / "PRODUCTION.md"
PIPELINE_DOC = ROOT / "deploy" / "STAGING_PIPELINE.md"
RELEASE_DOC = ROOT / "deploy" / "RELEASE.md"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish-staging-images.yml"
WORKFLOWS = ROOT / ".github" / "workflows"
SETUP_SCRIPT = ROOT / "deploy" / "github" / "setup-environments.sh"

DEPLOY_NAME = "deploy/DEPLOY.md"
PRODUCTION_NAME = "deploy/PRODUCTION.md"
PIPELINE_NAME = "deploy/STAGING_PIPELINE.md"

# (file, heading with its marks) of an old document -> (file, heading with its marks) of its new
# home. Empty until the sections move (the next two changes of this series fill it).
MOVED: dict[tuple[str, str], tuple[str, str]] = {}

# Old headings that stay in the old file, with a pointer line, because code, tests or other
# documents point at them. A heading that stays unchanged needs no stub and is in neither set; one
# that moves and is pointed at is in MOVED and here; one that moves unpointed is only in MOVED.
KEEP_STUB: set[tuple[str, str]] = set()

RELEASE_HISTORY_HEADING = "#### First publication: PR #11 merge images (2026-10-22)"


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Comment prefixes stripped, all whitespace collapsed: sentences wrap across lines."""
    lines = (re.sub(r"^\s*#+ ?", "", line) for line in text.splitlines())
    return " ".join(" ".join(lines).split())


def _headings(text: str) -> list[tuple[int, str, int]]:
    """``(level, heading text, line number)`` of every heading outside a code fence."""
    found = []
    fenced = False
    for number, line in enumerate(text.splitlines()):
        if line.startswith("```"):
            fenced = not fenced
        match = None if fenced else re.match(r"(#{1,6}) (.+?)\s*$", line)
        if match:
            found.append((len(match.group(1)), match.group(2), number))
    return found


def _has_heading(text: str, heading: str) -> bool:
    match = re.fullmatch(r"(#+) (.+)", heading)
    assert match, heading
    return any(
        (level, title) == (len(match.group(1)), match.group(2))
        for level, title, _ in _headings(text)
    )


def _section(text: str, heading: str) -> str:
    """The section whose heading line is exactly ``heading`` (marks included), up to the next
    heading of the same or a higher level."""
    match = re.fullmatch(r"(#+) (.+)", heading)
    assert match, heading
    level = len(match.group(1))
    lines = text.splitlines()
    heads = _headings(text)
    starts = [n for lv, title, n in heads if (lv, title) == (level, match.group(2))]
    assert len(starts) == 1, f"expected one '{heading}', found {len(starts)}"
    end = next((n for lv, _, n in heads if n > starts[0] and lv <= level), len(lines))
    return "\n".join(lines[starts[0] : end])


# --- the moved and stubbed headings --------------------------------------------------------------


def test_moved_headings_exist_at_their_destination_and_stubbed_ones_keep_a_pointer() -> None:
    for (_, _), (destination_file, destination_heading) in MOVED.items():
        assert _has_heading(_read(destination_file), destination_heading), destination_heading

    for old_file, old_heading in KEEP_STUB:
        destination_file, destination_heading = MOVED[(old_file, old_heading)]
        assert destination_file == DEPLOY_NAME, (old_file, old_heading)
        title = destination_heading.split(" ", 1)[1]
        pointer = f'Moved to deploy/DEPLOY.md, "{title}".'
        text = _read(old_file)
        assert _has_heading(text, old_heading), (old_file, old_heading)
        assert pointer in _section(text, old_heading), (old_file, old_heading, pointer)


# --- DEPLOY.md ----------------------------------------------------------------------------------

REQUIRED_HEADINGS = [
    "# Deploying CSA: staging and production",
    "## The pipeline",
    "## The routine for one release",
    "## Staging: automatic rollouts (deploy-staging)",
    "## Production: deploying from GitHub",
    "### What a smoke stop or a rollback means",
    "### What a failure means",
    "### What each refusal means",
    "## Auto-planned production runs (PROD_AUTO_PLAN)",
    "## Rollback",
    "## One-time setup",
    "### The AWS side: deploy/aws/setup-target.sh",
    "### The GitHub side: deploy/github/setup-environments.sh",
    "### The host side (per target)",
    "### Order and checks",
    "## Break-glass",
    "## Where each fact lives",
]


def _deploy() -> str:
    return DEPLOY_DOC.read_text(encoding="utf-8")


def test_deploy_doc_has_its_required_headings() -> None:
    found = [("#" * level) + " " + title for level, title, _ in _headings(_deploy())]
    # Exactly the required headings, in this order (later changes add the moved sections' own
    # lower-level headings, so only levels one and two and the listed three are compared).
    wanted = set(REQUIRED_HEADINGS)
    required_only = [heading for heading in found if heading in wanted]
    assert required_only == REQUIRED_HEADINGS
    assert found.count(REQUIRED_HEADINGS[0]) == 1
    assert sum(1 for heading in found if heading.startswith("# ")) == 1


def _setup_script_words() -> tuple[set[str], set[str]]:
    """Flags from the leading comment block (the usage) of the setup script and every secret,
    variable and environment name it holds, when the script exists."""
    flags: set[str] = set()
    names: set[str] = set()
    if not SETUP_SCRIPT.exists():
        return flags, names
    text = SETUP_SCRIPT.read_text(encoding="utf-8")
    usage = text.split("set -euo pipefail", 1)[0]
    flags = set(re.findall(r"--[a-z][a-z-]*", usage))
    names = set(re.findall(r"\b(?:STAGING|PROD)_[A-Z]+(?:_[A-Z]+)*\b|\bIMAGE_NAMESPACE\b", text))
    return flags, names


def _workflow_reads(path: Path) -> tuple[set[str], set[str], set[str]]:
    """Secrets, variables and environments one workflow reads (``GITHUB_TOKEN`` excluded)."""
    text = path.read_text(encoding="utf-8")
    secrets = set(re.findall(r"secrets\.([A-Z][A-Z0-9_]*)", text)) - {"GITHUB_TOKEN"}
    variables = set(re.findall(r"vars\.([A-Z][A-Z0-9_]*)", text))
    environments: set[str] = set()
    for job in (yaml.safe_load(text).get("jobs") or {}).values():
        environment = job.get("environment")
        if isinstance(environment, dict):
            environment = environment.get("name")
        if isinstance(environment, str):
            environments.add(environment)
    return secrets, variables, environments


def test_deploy_doc_names_every_setup_script_flag_secret_and_variable() -> None:
    text = _deploy()
    script_flags, script_names = _setup_script_words()
    flags = {
        "--check",
        "--repo",
        "--region",
        "--image-namespace",
        "--reviewer",
        "--replace-reviewers",
        "--auto-plan",
        "--paused",
    } | script_flags
    names = {
        "STAGING_DEPLOY_ROLE_ARN",
        "STAGING_INSTANCE_ID",
        "PROD_PLAN_ROLE_ARN",
        "PROD_APPLY_ROLE_ARN",
        "PROD_INSTANCE_ID",
        "PROD_CONSOLE_ORIGIN",
        "IMAGE_NAMESPACE",
        "STAGING_DEPLOY_REGION",
        "PROD_DEPLOY_REGION",
        "STAGING_AUTO_PAUSED",
        "PROD_AUTO_PLAN",
        "staging",
        "staging-images",
        "production-plan",
        "production",
    } | script_names
    for path in sorted(WORKFLOWS.glob("*.yml")):
        if path.name == "ci.yml":  # GITLEAKS_LICENSE and GITHUB_TOKEN: out of scope
            continue
        secrets, variables, environments = _workflow_reads(path)
        names |= secrets | variables | environments

    for word in sorted(flags | names):
        assert word in text, word
    assert "SETUP_" in text  # the secret values come from SETUP_<NAME> or a hidden prompt


def test_deploy_doc_explains_the_full_put_the_reread_and_the_separate_branch_policy_request() -> (
    None
):
    setup = _flat(_section(_deploy(), "### The GitHub side: deploy/github/setup-environments.sh"))

    for needle in (
        "not documented",
        "reviewers",
        "re-read",
        "can_admins_bypass",
        "--replace-reviewers",
        "separate",
        "deployment-branch-policies",
        "404",
    ):
        assert needle in setup, needle


def test_deploy_doc_covers_auto_plan_and_its_approval_guarantee() -> None:
    section = _flat(_section(_deploy(), "## Auto-planned production runs (PROD_AUTO_PLAN)"))

    for needle in (
        "PROD_AUTO_PLAN",
        "deploy-production-auto",
        "never approved by an agent",
        "review request",
        "concurrency",
        "skipped",
        "`plan` job still running",
        "Manual dispatch only",
        "nothing to plan:",
        "a deploy-production run for <sha7> already exists",
        "cancelled run <id> (planned <sha7>)",
        "left alone:",
        "warning: could not cancel",
        "started deploy-production run",
        "the dispatch was accepted but no deploy-production run started",
        "gh variable set PROD_AUTO_PLAN --repo amsyrzmi/CSAV2 --body true",
        "gh variable set PROD_AUTO_PLAN --repo amsyrzmi/CSAV2 --body false",
    ):
        assert needle in section, needle


def test_deploy_doc_carries_no_account_ids_addresses_arns_or_personal_names() -> None:
    text = _deploy()

    assert not re.search(r"\b\d{12}\b", text)
    assert not re.search(r"arn:aws:[a-z0-9-]*:[a-z0-9-]*:\d", text)
    assert not re.search(r"\b(?:mi|i)-[0-9a-f]{8,17}\b", text)
    assert not re.search(r"\b(?:us|eu|ap|sa|ca|me|af|il|mx)-[a-z]+-\d\b", text)
    addresses = re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)
    assert [address for address in addresses if not address.startswith("127.")] == []
    assert "fourstacks.my" not in text
    assert "fourstacks-csa-prod" not in text
    assert not re.search(r"\b(?:mito|webhooks)\.[a-z]", text)
    for needle in ("/tmp/claude-", "scratchpad/", "session_01", "Ams" + "yar"):
        assert needle not in text, needle


# --- the stale wording: the image publish needs no approval today ---------------------------------

STALE_PHRASES = (
    "owner approves",
    "owner's approval on",
    "waits for the owner",
    "approval is still required",
    "with a required reviewer",
)
STALE_SUBJECTS = ("staging-images", "image publish")
STALE_EXEMPTION = "If a reviewer is added to"


def _stale_sentences(text: str) -> list[str]:
    """Sentences of ``text`` (flattened, split at a full stop and a space) that say the owner
    approves the image publish, unless the sentence says what a later reviewer would do."""
    return [
        sentence
        for sentence in _flat(text).split(". ")
        if any(subject in sentence for subject in STALE_SUBJECTS)
        and any(phrase in sentence for phrase in STALE_PHRASES)
        and STALE_EXEMPTION not in sentence
    ]


def _without_release_history(text: str) -> str:
    """RELEASE.md without its dated first-publication record, which truthfully says the owner
    created ``staging-images`` with a required reviewer on that date."""
    lines = text.splitlines()
    heads = _headings(text)
    start = next(
        n for level, title, n in heads if "#" * level + " " + title == RELEASE_HISTORY_HEADING
    )
    level = 4
    end = next((n for lv, _, n in heads if n > start and lv <= level), len(lines))
    return "\n".join(lines[:start] + lines[end:])


def test_the_stale_guard_flags_a_violation_and_spares_the_sentence_about_a_later_reviewer() -> None:
    wrapped = (
        "# The publish is manual and the environment approval is\n# still required either way."
    )
    assert _stale_sentences("Publishing needs `staging-images`; " + wrapped) != []
    assert _stale_sentences("The owner approves the image publish on GitHub.") != []
    assert _stale_sentences("Before dispatch, staging-images with a required reviewer.") != []
    assert _stale_sentences("The `staging-images` environment waits for the owner. Next.") != []
    # An unrelated sentence, and the verbatim sentence of the fix, are not violations.
    assert _stale_sentences("The owner approves the production deployment.") == []
    verbatim = (
        "The image publish needs no approval today (the `staging-images` environment has no "
        "reviewer). If a reviewer is added to `staging-images`, every merge waits on that click."
    )
    assert _stale_sentences(verbatim) == []
    assert (
        _stale_sentences("If a reviewer is added to staging-images, the owner approves it.") == []
    )


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="fixed by Tasks 11 and 12")
def test_no_doc_or_workflow_claims_the_owner_approves_the_publish() -> None:
    texts = {
        RELEASE_DOC: _without_release_history(RELEASE_DOC.read_text(encoding="utf-8")),
        PIPELINE_DOC: PIPELINE_DOC.read_text(encoding="utf-8"),
        PRODUCTION_DOC: PRODUCTION_DOC.read_text(encoding="utf-8"),
        DEPLOY_DOC: _deploy(),
        PUBLISH_WORKFLOW: PUBLISH_WORKFLOW.read_text(encoding="utf-8"),
    }
    for skill in sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md")):
        texts[skill] = skill.read_text(encoding="utf-8")

    violations = {
        str(path.relative_to(ROOT)): _stale_sentences(text)
        for path, text in texts.items()
        if _stale_sentences(text)
    }
    assert violations == {}


def test_the_release_history_section_the_stale_guard_exempts_still_exists() -> None:
    text = RELEASE_DOC.read_text(encoding="utf-8")
    headings = _headings(text)
    spelled = ["#" * level + " " + title for level, title, _ in headings]
    assert spelled.count(RELEASE_HISTORY_HEADING) == 1

    section = _section(text, RELEASE_HISTORY_HEADING)
    assert "required reviewer" in section
    # The section ends at the next heading of the same or a higher level (today the staging host
    # image pull section), so the exemption cannot silently swallow what follows it.
    start = spelled.index(RELEASE_HISTORY_HEADING)
    ends = [heading for heading in headings[start + 1 :] if heading[0] <= 4]
    assert ends
    remaining = _without_release_history(text)
    assert RELEASE_HISTORY_HEADING not in remaining
    assert f"{'#' * ends[0][0]} {ends[0][1]}" in remaining
    assert ends[0][1] not in section
