"""deploy/DEPLOY.md, the entry runbook: its headings, what it must name, and the moved sections.

Two older documents, ``deploy/PRODUCTION.md`` and ``deploy/STAGING_PIPELINE.md``, give whole
generic sections to DEPLOY.md and keep a heading and a pointer where something points at them.
``MOVED`` and ``KEEP_STUB`` hold exactly what must stay findable; they do not freeze every heading
of the old files, because feature rollout sections there are edited with each feature. That
nothing is dropped in a move is checked by a one-off line-by-line comparison made when the move
happens; it is not a test and is not kept in the repository.

Also here: the stale-wording guard (the image publish needs no approval today), which fails when
a document, a skill or a workflow header claims the owner approves the publish, and the scan that
keeps real identifiers out of the public repository.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_DOC = ROOT / "deploy" / "DEPLOY.md"
PRODUCTION_DOC = ROOT / "deploy" / "PRODUCTION.md"
PIPELINE_DOC = ROOT / "deploy" / "STAGING_PIPELINE.md"
RELEASE_DOC = ROOT / "deploy" / "RELEASE.md"
WORKFLOWS = ROOT / ".github" / "workflows"
AUTO_WORKFLOW = WORKFLOWS / "deploy-production-auto.yml"
SETUP_SCRIPT = ROOT / "deploy" / "github" / "setup-environments.sh"

DEPLOY_NAME = "deploy/DEPLOY.md"
PRODUCTION_NAME = "deploy/PRODUCTION.md"
PIPELINE_NAME = "deploy/STAGING_PIPELINE.md"

# (file, heading with its marks) of an old document -> (file, heading with its marks) of its new
# home: production's generic sections and staging's automatic-rollout section.
MOVED: dict[tuple[str, str], tuple[str, str]] = {
    (PRODUCTION_NAME, "## Deploying from GitHub"): (
        DEPLOY_NAME,
        "## Production: deploying from GitHub",
    ),
    (PRODUCTION_NAME, "### What a smoke stop or a rollback means"): (
        DEPLOY_NAME,
        "### What a smoke stop or a rollback means",
    ),
    (PRODUCTION_NAME, "### What a failure means"): (DEPLOY_NAME, "### What a failure means"),
    (PRODUCTION_NAME, "### What each refusal means"): (DEPLOY_NAME, "### What each refusal means"),
    (PIPELINE_NAME, "### Automatic rollouts (deploy-staging)"): (
        DEPLOY_NAME,
        "## Staging: automatic rollouts (deploy-staging)",
    ),
}

# Old headings that stay in the old file, with a pointer line, because code, tests or other
# documents point at them. A heading that stays unchanged needs no stub and is in neither set; one
# that moves and is pointed at is in MOVED and here; one that moves unpointed is only in MOVED.
KEEP_STUB: set[tuple[str, str]] = {
    (PRODUCTION_NAME, "## Deploying from GitHub"),
    (PIPELINE_NAME, "### Automatic rollouts (deploy-staging)"),
}

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

    assert KEEP_STUB <= set(MOVED), "every stubbed heading must also be listed in MOVED"
    for old_file, old_heading in KEEP_STUB:
        destination_file, destination_heading = MOVED[(old_file, old_heading)]
        assert destination_file == DEPLOY_NAME, (old_file, old_heading)
        title = destination_heading.split(" ", 1)[1]
        pointer = f'Moved to deploy/DEPLOY.md, "{title}".'
        text = _read(old_file)
        assert _has_heading(text, old_heading), (old_file, old_heading)
        # _section includes child headings, so a pointer under a `###` child (or inside a code
        # fence or an HTML comment) would also pass. Accepted: the pointers are written by hand
        # and read in review; this test keeps the heading and the pointer from vanishing.
        assert pointer in _section(text, old_heading), (old_file, old_heading, pointer)


def test_the_manual_setup_steps_the_script_tests_read_still_exist() -> None:
    # tests/test_aws_setup_target.py cuts PRODUCTION.md between these heading prefixes and compares
    # the manual commands with setup-target.sh, so a later edit cannot orphan the steps silently.
    titles = [title for level, title, _ in _headings(_read(PRODUCTION_NAME)) if level == 3]
    for prefix in (
        "1. AWS (CloudShell, admin login)",
        "2. Instance",
        "3. AWS (CloudShell)",
        "4. AWS (CloudShell)",
        "5. Instance",
    ):
        assert sum(title.startswith(prefix) for title in titles) == 1, prefix


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


SETUP_FLAGS = {
    "--check",
    "--repo",
    "--region",
    "--image-namespace",
    "--reviewer",
    "--replace-reviewers",
    "--auto-plan",
    "--paused",
}


def _setup_script_words() -> tuple[set[str], set[str]]:
    """Flags of the synopsis of the setup script and the environment, secret and variable names
    of its target tables, when the script exists.

    Only the synopsis lines are read for flags (the comment lines that start at
    ``setup-environments.sh`` and run to the first bare ``#``), so a ``--word`` in the prose of
    the header or in a ``gh api`` call does not have to be documented; and only the quoted rows
    of the ENVIRONMENTS, SECRETS and VARIABLES tables are read for names, so a shell variable
    that happens to start with ``PROD_`` does not either.
    """
    if not SETUP_SCRIPT.exists():
        return set(), set()
    text = SETUP_SCRIPT.read_text(encoding="utf-8")
    synopsis: list[str] = []
    collecting = False
    for line in text.splitlines():
        if line.startswith("set -euo pipefail"):
            break
        if re.match(r"#\s+setup-environments\.sh\b", line):
            collecting = True
        elif collecting and not re.match(r"#\s+\S", line):
            break
        if collecting:
            synopsis.append(line)
    flags = set(re.findall(r"--[a-z][a-z-]*", "\n".join(synopsis)))
    names = set(re.findall(r'^\s*"[a-z-]+\|([A-Z][A-Z_]*)"', text, re.MULTILINE))  # SECRETS
    names |= set(
        re.findall(r'^\s*"([A-Z][A-Z_]*)\|(?:namespace|region|switch)\|', text, re.MULTILINE)
    )  # VARIABLES
    names |= set(re.findall(r'^\s*"([a-z-]+)\|(?:required|preserved)\|', text, re.MULTILINE))
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
    if SETUP_SCRIPT.exists():
        # The parse must not silently find nothing (a changed header would otherwise leave only
        # the hard-coded list), and the script must not grow a flag the list does not know.
        assert script_flags, "no flag found in the synopsis of the setup script"
        assert script_flags <= SETUP_FLAGS, sorted(script_flags - SETUP_FLAGS)
        assert script_names, "no name found in the target tables of the setup script"
    flags = SETUP_FLAGS | script_flags
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
        "at most six",
        "`unset: off`",
        "reviewers on production: <old logins> -> <new logins>",
        "admin bypass: true -> false (changed)",
        "failed: <field> is <actual> after the write, expected <intended>",
        # The owner's decision of 2026-11-18 and the self-review rule.
        "`can_admins_bypass` is wanted `false` on `production` and `production-plan`",
        "`prevent_self_review` is never changed",
        # The line grammar of `--check`, and what `apply` prints (Task 4 of the change).
        "`[n/total] <what>: <result>`",
        "`ok (true)`",
        "`ok (unset: off)`",
        "`differs (<value>)`",
        "`differs (not true or false)`",
        "`note:` lines",
        "(hidden; Enter skips it; will be set on: <environments>)",
        "exported but empty is an error (exit 2)",
        "a role ARN with a path",
        "tracing off before it reads any value",
        "moves every `SETUP_<NAME>` out of the environment",
        # What the move does not cover (a name that is not a shell identifier, an empty name, and
        # the process's own /proc/<pid>/environ, which bash cannot change).
        "an environment name that is not one (for example `SETUP_A.B`) is never read and passes "
        "to child processes untouched",
        "an exported `SETUP_` with no name is ignored",
        "Bash cannot change the script's own `/proc/<pid>/environ`",
        "stay readable there by the same user and by root while the script runs",
        "use the hidden prompt when that matters",
        "it is counted as missing",
        "`secret <NAME> on <env>: set`",
        "`apply: <n> item(s) still missing`",
        "`apply: everything is in place`",
        "`variable PROD_AUTO_PLAN: set (true)`",
    ):
        assert needle in setup, needle


def test_the_setup_script_header_states_what_the_move_out_of_the_environment_does_not_cover() -> (
    None
):
    header = _flat(SETUP_SCRIPT.read_text(encoding="utf-8").split("set -euo pipefail", 1)[0])
    for needle in (
        "that is not a valid shell identifier, such as `SETUP_A.B`, is never a shell variable",
        "bash passes it to children untouched",
        "An exported `SETUP_` with an empty name names no secret and is dropped",
        "What bash cannot do is change the script's own /proc/<pid>/environ",
        "stay readable there by the same user and by root for as long as the script runs",
        "Use the hidden prompt when that matters",
    ):
        assert needle in header, needle


def test_the_auto_switch_wording_does_not_claim_a_case_sensitive_comparison() -> None:
    # GitHub compares strings without regard to letter case, so `TRUE` is on too.
    auto = _flat(_section(_deploy(), "## Auto-planned production runs (PROD_AUTO_PLAN)"))
    workflow = _flat(AUTO_WORKFLOW.read_text(encoding="utf-8").split("\nname:", 1)[0])
    for text in (auto, workflow):
        assert "any value but" not in text
        assert "any other value" not in text
    assert "anything that is not `true`, letter case ignored" in workflow


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
        "requested, queued, in progress, waiting, pending or already succeeded",
        "which is the tip of `main` when the run was dispatched, "
        "not necessarily the commit it plans",
        "makes `check` report that a run for T already exists, "
        "and the plan for T is then started by hand",
        "`supersede` cancels that run while it is waiting and the new plan starts",
        "cancelled run <id> (planned <sha7>)",
        "left alone:",
        "`started by <login>` (a person's run, including a rollback), `same commit`, "
        "`apply is not waiting`, `not a dispatch`, `not on main` or `unreadable fields`",
        "three `workflow_run` links",
        "is documented to create a run",
        "run <id> moved on; left alone",
        "warning: could not cancel",
        "started deploy-production run",
        "the dispatch was accepted but no deploy-production run started",
        "gh variable set PROD_AUTO_PLAN --repo amsyrzmi/CSAV2 --body true",
        "gh variable set PROD_AUTO_PLAN --repo amsyrzmi/CSAV2 --body false",
        # The limits of the cancel, stated plainly (decision 5 of the plan).
        "a window of one API round trip",
        "replaces the pending manual run",
        "does not stop an SSM command",
        "check the host log and the public smoke test by hand",
        "`deploy-production-auto` concurrency group",
        # The security statement (decision 6).
        "no `environment`, no `id-token` permission and no secret",
        "not one; the script and the workflow contain no call that approves",
        "uses no field of the triggering event in a command",
        "setup-environments.sh production --check --repo amsyrzmi/CSAV2",
        # The two ways to read the switch, and what the script's command needs and prints.
        "`gh variable get PROD_AUTO_PLAN --repo amsyrzmi/CSAV2`",
        "`[n/total] variable PROD_AUTO_PLAN: ok (true)`",
        "needs repository admin and exits 1 when any other item of the target is not ok",
        # The switch is read the way GitHub compares strings: letter case ignored.
        "Unset, or anything that is not `true` (letter case ignored), means off",
        # The unattended plan job of the auto-started run, and what is still unverified.
        "runs in `production-plan` (no reviewer, `main` only) with the plan AWS role, and sends a "
        "read-only plan to the production instance",
        "The `apply` job still waits for the human click on the `production` environment",
        "unverified until the first auto run",
        "expected to be `github-actions[bot]`",
        "(check that workflow_run chaining is not limited); look for a run before starting another",
    ):
        assert needle in section, needle


AUTO_PLAN_SCRIPT = ROOT / "deploy" / "production" / "auto_plan.sh"
# What `supersede` prints in the parentheses of `left alone: run <id> (<reason>)`, and the order
# DEPLOY.md lists them in.
LEFT_ALONE_REASONS = [
    "started by <login>",
    "same commit",
    "apply is not waiting",
    "not a dispatch",
    "not on main",
    "unreadable fields",
]


def _workflow_run_sources(path: Path) -> list[str]:
    """The workflows whose completion starts this one (``on.workflow_run.workflows``)."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    triggers = document.get("on", document.get(True)) or {}
    if isinstance(triggers, str | list):
        return []
    return list((triggers.get("workflow_run") or {}).get("workflows") or [])


def _workflow_run_depths() -> dict[str, int]:
    """The number of ``workflow_run`` links from a workflow that is not started by one to each
    workflow (``ci`` has none, ``publish-staging-images`` has one, and so on)."""
    sources = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        name = yaml.safe_load(path.read_text(encoding="utf-8"))["name"]
        sources[name] = _workflow_run_sources(path)

    def depth(name: str, seen: tuple[str, ...] = ()) -> int:
        assert name not in seen, f"workflow_run cycle through {name}"
        return max((depth(source, (*seen, name)) + 1 for source in sources[name]), default=0)

    return {name: depth(name) for name in sources}


def test_the_workflow_run_chain_is_three_links_and_the_doc_says_so() -> None:
    # GitHub runs at most three workflow_run links in a chain; a fourth would silently not start.
    depths = _workflow_run_depths()
    assert depths["ci"] == 0
    assert depths["publish-staging-images"] == 1
    assert depths["deploy-staging"] == 2
    assert depths["deploy-production-auto"] == 3
    assert max(depths.values()) == 3
    # The dispatched deploy-production is not a workflow_run link.
    assert depths["deploy-production"] == 0

    auto = _flat(_section(_deploy(), "## Auto-planned production runs (PROD_AUTO_PLAN)"))
    assert "three `workflow_run` links" in auto
    assert "`ci`, `publish-staging-images`, `deploy-staging` and this workflow" in auto
    header = _flat(AUTO_WORKFLOW.read_text(encoding="utf-8").split("\nname:", 1)[0])
    assert "three `workflow_run` links" in header


def test_the_six_left_alone_reasons_are_what_the_script_prints_and_the_doc_lists() -> None:
    script = AUTO_PLAN_SCRIPT.read_text(encoding="utf-8")
    printed = [
        reason.replace("$login", "<login>")
        for reason in re.findall(r'verdict="([^"]+)"', script)
        if reason != "cancel"
    ]
    assert sorted(set(printed)) == sorted(LEFT_ALONE_REASONS)

    auto = _flat(_section(_deploy(), "## Auto-planned production runs (PROD_AUTO_PLAN)"))
    (listed,) = re.findall(
        r"The reason is (.*?) \(a person's run, including a rollback\)(.*?)\.", auto
    )
    reasons = re.findall(r"`([^`]+)`", " ".join(listed))
    assert reasons == LEFT_ALONE_REASONS
    # The script's own header names the same six, in the same order.
    header = _flat(script.split("set -euo pipefail", 1)[0])
    assert "reason: " + ", ".join(LEFT_ALONE_REASONS) in header


# `four` within a few words of `services` ("all four services", "four app services", "the four
# stopped services"): both targets run five app services, and a count in prose goes stale.
FOUR_SERVICES = re.compile(r"\bfour\b(?:\W+\w+){0,3}?\W+services\b", re.IGNORECASE)


def test_the_four_services_pattern_sees_the_spellings_it_is_meant_to() -> None:
    for claim in (
        "A migration stops all four services.",
        "Stops the four app services.",
        "Of the Four stopped services, none runs.",
        "four\nservices",
    ):
        assert FOUR_SERVICES.search(claim), claim
    for fine in ("recreates the five services", "the four workflows", "fourstacks services"):
        assert not FOUR_SERVICES.search(fine), fine


def test_deploy_doc_states_the_facts_the_review_checked() -> None:
    text = _flat(_deploy())
    rollback = _flat(_section(_deploy(), "## Rollback"))
    routine = _flat(_section(_deploy(), "## The routine for one release"))
    auto = _flat(_section(_deploy(), "## Auto-planned production runs (PROD_AUTO_PLAN)"))

    # Both targets have five app services (scripts/rollout_targets.py); no count is safest. The
    # word `four` next to services, app services or the stack is flagged in any spelling.
    assert not FOUR_SERVICES.search(text)
    assert "stops the app services on staging" in routine
    assert "recreates the five services" in rollback
    # Production never writes a staged record (scripts/staging_rollout.py, PRODUCTION.md).
    assert "staged record is not written" not in text
    assert "nothing is recorded as deployed" in rollback
    # Restoring is a decision of the owner, not a step of the runbook.
    assert "is an owner decision, not a step" in rollback
    # The staging-images branch policy exists only after the setup script has run.
    assert "`main`-only once the setup script has run" in text
    # Wording of the staging workflow's own header, and the statuses that block `check`.
    assert "can be a later commit" in auto
    assert "is not the commit that run handled" not in text
    assert "queued, in progress, waiting, pending or already succeeded" in auto
    # A cancel after an approval can leave the instance deploying with no smoke test.
    assert "no workflow smoke test" in auto
    # The red outcomes of the chain are all listed, not one.
    assert "the one that means the chain itself failed" in auto
    assert "an unreadable run list" in auto
    assert "a refused dispatch" in auto
    # A cancelled deploy-production-auto run is normal.
    assert "a cancelled `deploy-production-auto` run" in auto
    # The ci row says it can be dispatched, and that the archives are not images in a registry.
    assert "a manual dispatch" in text
    assert "`packages: read`" in auto
    assert "at most one per merge" in auto
    # Order: the first apply ends non-zero until the secrets exist.
    order = _flat(_section(_deploy(), "### Order and checks"))
    assert "still missing" in order
    assert "expected until step 5" in order


FIVE_STAGING_SERVICES = ("gateway", "frontend", "worker", "delivery", "training-worker")


def test_the_migration_paragraphs_name_all_five_staging_services() -> None:
    # scripts/rollout_targets.py STAGING.stop_services is the truth; a count that goes stale or a
    # name that is dropped (the training worker was the one forgotten) would not trip FOUR_SERVICES.
    deploy = _flat(_section(_deploy(), "## Staging: automatic rollouts (deploy-staging)"))
    paragraph = deploy.split("**A migration stops the app services on every such merge.**", 1)[1]
    paragraph = paragraph.split("**Pause and catch up.**", 1)[0]
    for name in FIVE_STAGING_SERVICES:
        assert f"`{name}`" in paragraph, name
    pipeline = _section(
        _read(PIPELINE_NAME), "### Recovery, retest and switches (automatic and manual rollouts)"
    )
    flat_pipeline = _flat(pipeline)
    lead = flat_pipeline.split("**Recovery.**", 1)[0]
    assert "stops all five services" in lead
    for name in FIVE_STAGING_SERVICES:
        assert f"`{name}`" in lead, name
    # The copy-ready restart line the script prints names the five containers in this order.
    containers = " ".join(f"csa-staging-{name}-1" for name in FIVE_STAGING_SERVICES)
    assert containers in flat_pipeline
    # No count of four services anywhere in the staging runbook (the versions and steps that are
    # four are not services).
    assert not FOUR_SERVICES.search(_flat(_read(PIPELINE_NAME)))


def test_the_host_setup_steps_say_what_the_check_prints_before_the_script_has_been_applied() -> (
    None
):
    setup = _flat(_section(_read(PIPELINE_NAME), "## Host setup for automatic rollouts"))
    # Step 2 runs --check before step 13 sets the secrets and the region variable: it ends with
    # `check: <n> item(s) not ok` and exit 1, and the text says so and points at step 13.
    assert "At this step it also reports the secrets and the region variable as missing" in setup
    assert "(they are set at step 13)" in setup
    assert "`check: <n> item(s) not ok` is expected here" in setup
    # In step 13 the manual commands are the code block above the script's command.
    assert "the manual commands above remain above it for the same effect" in setup
    assert "remain below it" not in setup


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

# The guard reads one sentence at a time and flags a sentence that names the image publish AND says
# the owner approves it (or that it has a required reviewer). Its limits, accepted: a claim that
# spreads over two sentences ("The publish needs a reviewer. The owner clicks.") or names the
# publish only by "it" is not seen; a table row is one block (a subject and a phrase in two cells
# of one row are seen, in two rows they are not); a negated sentence ("the owner never approves
# the image publish") is flagged like the claim, so write the true statement the way DEPLOY.md
# does ("needs no approval today"); and a paraphrase the patterns do not know passes. Case, the
# curly apostrophe, wrapped lines, comment prefixes and `e.g.` are handled. Text inside a code fence
# is read like any other text, so a code fence that contains stale wording is still flagged (the
# safer side: the claim is wrong wherever it is written).
STALE_SUBJECTS = re.compile(r"staging-images|image publish|the publish\b|publish(?:ing)? images?")
STALE_PHRASES = re.compile(
    "|".join(
        (
            r"\bowner(?:'s)?\s+(?:\w+\s+){0,2}approv\w*",
            r"\bapproved by the owner\b",
            r"\bwaits? for the owner\b",
            r"\bapproval is (?:still )?required\b",
            r"\brequired reviewers?\b",
        )
    )
)
STALE_EXEMPTION = "if a reviewer is added to"
_LIST_ITEM = re.compile(r"\s*(?:[-*] |\d+\. )")


def _blocks(text: str, *, markdown: bool) -> list[str]:
    """The text cut into blocks that cannot share a sentence, each flattened to one line: a
    heading alone, a table row, a list item (with its wrapped lines) and a paragraph. Comment
    prefixes (``# ``) are stripped and wrapped lines joined; in a markdown file ``#`` at the start
    of a line outside a code fence is a heading, in anything else it is a comment."""
    blocks: list[str] = []
    current: list[str] = []
    fenced = False

    def close() -> None:
        if current:
            blocks.append(" ".join(" ".join(current).split()))
            current.clear()

    for line in text.splitlines():
        if markdown and line.startswith("```"):
            fenced = not fenced
            close()
            continue
        if markdown and not fenced and re.match(r"#{1,6} ", line):
            close()
            blocks.append(line.lstrip("#").strip())
            continue
        body = re.sub(r"^\s*#+ ?", "", line)
        if not body.strip():
            close()
        elif body.lstrip().startswith("|"):
            close()
            blocks.append(" ".join(body.split()))  # one table row is one block
        else:
            if _LIST_ITEM.match(body):
                close()
            current.append(body)
    close()
    return blocks


def _sentences(block: str) -> list[str]:
    """Lower-cased sentences of one block, split at a full stop, ``?`` or ``!`` and a space (not
    after ``e.g.`` or ``i.e.``), with the curly apostrophes made straight."""
    block = block.casefold().replace("\u2019", "'").replace("\u2018", "'")
    block = re.sub(r"\b(e\.g|i\.e)\.(?=\s)", lambda m: m.group(1).replace(".", ""), block)
    return re.split(r"[.!?]\s+", block)


def _stale_sentences(text: str, *, markdown: bool = True) -> list[str]:
    """Sentences of ``text`` that say the owner approves the image publish (or that it has a
    required reviewer), unless the sentence says what a later reviewer would do."""
    return [
        sentence
        for block in _blocks(text, markdown=markdown)
        for sentence in _sentences(block)
        if STALE_SUBJECTS.search(sentence)
        and STALE_PHRASES.search(sentence)
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
    # Wrapped comment lines of a workflow header (not markdown: there `# ` would be a heading).
    assert _stale_sentences("Publishing needs `staging-images`; " + wrapped, markdown=False) != []
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


def test_the_stale_guard_sees_the_other_ways_to_say_it() -> None:
    for claim in (
        "Owner approves the image publish.",  # sentence-initial capital
        "Waits for the owner on `staging-images`.",
        "The owner\u2019s approval on `staging-images` is needed.",  # curly apostrophe
        "The owner will approve the image publish.",
        "The owner will then approve each image publish.",
        "The `staging-images` environment has required reviewers.",  # plural
        "The owner approves the publish.",  # no word 'image'
        "Publishing images needs the owner to approve it, so the owner approves each run.",
        "Each `staging-images` run is approved by the owner.",
        "The `staging-images` environment, e.g. the owner approves each publish, is open.",
    ):
        assert _stale_sentences(claim) != [], claim


def test_the_stale_guard_reads_headings_tables_and_bullets_one_block_at_a_time() -> None:
    table = (
        "| Workflow | Environments | Who clicks |\n"
        "| --- | --- | --- |\n"
        "| `publish-staging-images` | `staging-images` (no reviewer) | nobody |\n"
        "| `deploy-production` | `production` | the owner approves the `apply` |\n"
    )
    assert _stale_sentences(table) == []
    row = "| `publish-staging-images` | `staging-images` | the owner approves the `apply` |\n"
    assert _stale_sentences(row) != []
    assert _stale_sentences("| `staging-images` | nobody |\n| other | the owner approves |\n") == []
    # A heading is its own block: it does not merge into the first sentence under it.
    assert (
        _stale_sentences("## The `staging-images` environment\nThe owner approves the apply.") == []
    )
    assert _stale_sentences("## The owner approves\n\nThe image publish needs no approval.") == []
    bullets = "- `staging-images` has no reviewer\n- The owner approves the apply\n"
    assert _stale_sentences(bullets) == []
    assert _stale_sentences("- the owner approves the staging-images job\n- another\n") != []
    numbered = "1. The `staging-images` run\n2. The owner approves the apply\n"
    assert _stale_sentences(numbered) == []
    # A wrapped list item is one block.
    wrapped = "- the `staging-images` job\n  waits for the owner\n"
    assert _stale_sentences(wrapped) != []
    # Text inside a code fence is read like any other: stale wording in a fence is still flagged.
    fenced = "```\nthe owner approves the staging-images job\n```\n"
    assert _stale_sentences(fenced) != []
    # In a file that is not markdown, `# ` is a comment, not a heading.
    comment = "# Publishing images, `staging-images`,\n# waits for the owner.\n"
    assert _stale_sentences(comment, markdown=False) != []


def test_no_doc_or_workflow_claims_the_owner_approves_the_publish() -> None:
    texts = {
        RELEASE_DOC: _without_release_history(RELEASE_DOC.read_text(encoding="utf-8")),
        PIPELINE_DOC: PIPELINE_DOC.read_text(encoding="utf-8"),
        PRODUCTION_DOC: PRODUCTION_DOC.read_text(encoding="utf-8"),
        DEPLOY_DOC: _deploy(),
    }
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        texts[workflow] = workflow.read_text(encoding="utf-8")
    for skill in sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md")):
        texts[skill] = skill.read_text(encoding="utf-8")

    violations = {
        str(path.relative_to(ROOT)): _stale_sentences(text, markdown=path.suffix == ".md")
        for path, text in texts.items()
        if _stale_sentences(text, markdown=path.suffix == ".md")
    }
    assert violations == {}


def test_deploy_doc_itself_never_claims_the_owner_approves_the_publish() -> None:
    assert _stale_sentences(_deploy()) == []


def test_no_workflow_header_claims_the_owner_approves_the_publish() -> None:
    # Every workflow file is read (not only the publish one), so a new workflow's header, such as
    # the auto-plan workflow's, is covered; the stale claim cannot come back in any of them.
    workflows = sorted(WORKFLOWS.glob("*.yml"))
    assert {path.name for path in workflows} >= {
        "ci.yml",
        "deploy-production-auto.yml",
        "deploy-production.yml",
        "deploy-staging.yml",
        "publish-staging-images.yml",
    }
    for path in workflows:
        assert _stale_sentences(path.read_text(encoding="utf-8"), markdown=False) == [], path.name


PLACEHOLDER = re.compile(
    r"completed\s+by\s+the\s+next\s+change|next\s+change\s+in\s+this\s+series|"
    r"\bto\s+be\s+(?:written|completed)\b",
    re.IGNORECASE,
)


def test_no_placeholder_sections_remain() -> None:
    # The text is flattened first, so a placeholder sentence cannot hide by wrapping across lines.
    text = _flat(_deploy())
    assert PLACEHOLDER.search(text) is None, PLACEHOLDER.search(text)
    assert PLACEHOLDER.search("This section is completed by\nthe next change in this series.")
    # The two sections that were placeholders hold their text (their needles are asserted in
    # tests/test_production_deploy_assets.py and tests/test_staging_rollout.py).
    for heading in (
        "## Staging: automatic rollouts (deploy-staging)",
        "## Production: deploying from GitHub",
    ):
        assert len(_flat(_section(_deploy(), heading))) > 2000, heading


def test_the_release_history_section_the_stale_guard_exempts_still_exists() -> None:
    text = RELEASE_DOC.read_text(encoding="utf-8")
    headings = _headings(text)
    spelled = ["#" * level + " " + title for level, title, _ in headings]
    assert spelled.count(RELEASE_HISTORY_HEADING) == 1

    section = _section(text, RELEASE_HISTORY_HEADING)
    # The exemption is real: the guard would flag this section if it were not exempt.
    assert _stale_sentences(section) != []
    # The section ends at the next heading of the same or a higher level (today the staging host
    # image pull section), so the exemption cannot silently swallow what follows it.
    start = spelled.index(RELEASE_HISTORY_HEADING)
    ends = [heading for heading in headings[start + 1 :] if heading[0] <= 4]
    assert ends
    remaining = _without_release_history(text)
    assert RELEASE_HISTORY_HEADING not in remaining
    assert f"{'#' * ends[0][0]} {ends[0][1]}" in remaining
    assert ends[0][1] not in section


def test_production_doc_points_the_image_namespace_at_the_moved_paragraph() -> None:
    # The IMAGE_NAMESPACE paragraph moved to DEPLOY.md with the section; the stub names neither it
    # nor the variable, so the pointer in the setup list must name the new home, not the old
    # heading (which is now a stub).
    text = _flat(_read(PRODUCTION_NAME))
    start = text.index("Repository variable **`IMAGE_NAMESPACE`**")
    bullet = text[start : text.index("if the repository moves.", start)]
    assert '`deploy/DEPLOY.md`, "Production: deploying from GitHub"' in bullet
    assert "see Deploying from GitHub" not in bullet
    assert "IMAGE_NAMESPACE" in _flat(_section(_deploy(), "## Production: deploying from GitHub"))


# --- the deploy skill ----------------------------------------------------------------------------

DEPLOY_SKILL = ROOT / ".claude" / "skills" / "deploy" / "SKILL.md"
_OLD_SKILL = r"(?:staging-rollout|production-deploy)"
# A path, a "<name> skill" or "skill <name>" with any quoting or punctuation between, the
# `<name>/SKILL.md` file, or a frontmatter `name:` line, in any letter case.
OLD_SKILL_NAMES = re.compile(
    rf"skills/{_OLD_SKILL}|{_OLD_SKILL}/SKILL|{_OLD_SKILL}\W{{0,3}}skill\b|"
    rf"\bskill\W{{0,3}}{_OLD_SKILL}|name:\s*{_OLD_SKILL}\b",
    re.IGNORECASE,
)


def _skill_text() -> str:
    return DEPLOY_SKILL.read_text(encoding="utf-8")


def test_the_deploy_skill_description_triggers_on_both_flows() -> None:
    # The frontmatter is parsed, not searched: a plain scalar holding ': ' or ' #' is a YAML
    # error, the skill would not load, and a substring test cannot see that.
    text = _skill_text()
    assert text.startswith("---\n")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    assert isinstance(frontmatter, dict)
    assert frontmatter["name"] == "deploy"
    description = frontmatter["description"]
    assert isinstance(description, str)
    assert description.startswith("Use when the owner asks to roll out, deploy or update staging")
    for needle in (
        "roll out",
        "deploy",
        "update",
        "staging",
        "production",
        "deploy-staging",
        "deploy-production",
        "migrations",
    ):
        assert needle in description, needle


def test_the_old_skill_directories_are_gone_and_nothing_live_names_them() -> None:
    skills = ROOT / ".claude" / "skills"
    assert not (skills / "staging-rollout").exists()
    assert not (skills / "production-deploy").exists()
    assert DEPLOY_SKILL.is_file()
    # A bare `staging-rollout` is not the needle: deploy/staging-rollout.env and the lock name
    # csa-staging-rollout.lock are live. Older specs and plans (docs/superpowers) are history.
    this_file = Path(__file__).resolve()
    roots = [
        ROOT / ".claude" / "skills",
        ROOT / "deploy",
        ROOT / "tests",
        ROOT / "scripts",
        ROOT / ".github",
    ]
    files = [
        ROOT / "docs" / "RUNBOOK.md",
        ROOT / "docs" / "PROJECT_OVERVIEW.md",
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
        ROOT / "Makefile",
    ]
    for root in roots:
        files.extend(path for path in sorted(root.rglob("*")) if path.is_file())
    found = []
    for path in files:
        if path.resolve() == this_file or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if OLD_SKILL_NAMES.search(text):
            found.append(str(path.relative_to(ROOT)))
    assert found == []
    # The pattern catches what it forbids.
    for sample in (
        ".claude/skills/staging-rollout/SKILL.md",
        "the `production-deploy` skill",
        "name: staging-rollout",
        "the staging-rollout skill",
        "Production-Deploy skill",
        "skill 'production-deploy'",
        "production-deploy/SKILL.md",
    ):
        assert OLD_SKILL_NAMES.search(sample), sample
    assert not OLD_SKILL_NAMES.search("deploy/staging-rollout.env csa-staging-rollout.lock")


CANCEL_RULE = (
    "cancel only a run whose apply job is still `waiting` (read in the same turn); never one "
    "whose apply is approved, queued, `in_progress` or finished"
)


def test_the_deploy_skill_never_approves_and_checks_state_before_cancelling() -> None:
    skill = _skill_text()
    flat = _flat(skill)
    for needle in (
        "never approve",
        "never reject",
        "read its state",
        "in_progress",
        "PROD_AUTO_PLAN",
        "owner's go",
    ):
        assert needle in flat, needle
    # The cancel rule is stated positively and identically where a run is waiting and in Never.
    auto = _flat(_section(skill, "## Auto-planned production runs"))
    never = _flat(_section(skill, "## Never"))
    assert CANCEL_RULE in auto
    assert CANCEL_RULE in never
    assert flat.count(CANCEL_RULE) == 2
    # An agent never cancels a production run itself (cancelling a waiting apply is a reject in
    # effect): it hands the owner the command, and the positive rule is what the owner checks.
    assert (
        "Never cancel a production run yourself, with or without the owner's go: cancelling a "
        "waiting apply is a reject in effect." in auto
    )
    assert "`gh run cancel <run id> --repo amsyrzmi/CSAV2`, for the owner to run" in auto
    assert (
        "Cancel a production run yourself, with or without the owner's go: give the owner the "
        "command to run, and say that the owner should cancel only a run whose apply job" in never
    )
    assert "The only automatic cancel is the workflow's own `supersede`" in auto
    for old in (
        "Cancel a production run only after the owner's go",
        "Cancel a production run without the owner's go",
    ):
        assert old not in flat, old
    # Approving or rejecting has its own bullet and holds with or without the owner's go.
    bullets = [" ".join(chunk.split()) for chunk in _section(skill, "## Never").split("\n- ")[1:]]
    approve = next(b for b in bullets if b.startswith("Approve or reject"))
    assert approve == (
        "Approve or reject a production deployment, or ask anyone to approve for the owner. "
        "Never, with or without the owner's go."
    )
    # "on your own" is left only for PROD_AUTO_PLAN; the rest of the setup is a flat never.
    own = [b for b in bullets if "on your own" in b]
    assert len(own) == 1 and "`PROD_AUTO_PLAN`" in own[0]
    setup = next(b for b in bullets if b.startswith("Change the environments"))
    assert setup.endswith("Never.") and "PROD_AUTO_PLAN" not in setup
    for needle in ("their reviewers or branch rules", "the secrets", "the AWS roles and documents"):
        assert needle in setup, needle
    # A rerun of a production run re-uses the old plan.
    assert any(b.startswith("Run `gh run rerun` on a production run:") for b in bullets)
    assert "a rerun re-uses the old plan" in never


def test_the_deploy_skill_keeps_the_rules_that_are_not_about_approval() -> None:
    skill = _skill_text()
    never = _flat(_section(skill, "## Never"))
    staging = _flat(_section(skill, "## Staging: automatic rollouts"))
    manual = _flat(_section(skill, "## Staging: manual rollout (break-glass)"))
    steps = _flat(_section(skill, "## Production: steps"))
    wrong = _flat(_section(skill, "## If something goes wrong"))
    # Secrets and the host file stay unread; the staged gate has no way round.
    assert "anything under `/srv/csa-staging/secrets`" in never
    assert "`/srv/csa-prod/host.env` values" in never
    assert "Bypass the staged gate or suggest a way around it" in never
    # Setup is the owner's, with the two scripts, and no value is set by the agent.
    assert "`deploy/github/setup-environments.sh`" in wrong
    assert "Do not run either script, and do not set a secret value" in wrong
    # Starting a run is the owner's go, which is not the approval.
    assert "Dispatch only after the owner's go" in staging
    assert "Dispatch only after the owner's go" in steps
    assert "it is not the approval in step 4" in steps
    # A manual rollout pauses staging first and unpauses after.
    assert "Pause first (`STAGING_AUTO_PAUSED` `true`), unpause after." in manual
    assert "pause first, unpause after" in staging
    # A stale waiting run is the owner's to reject; a failed record job is re-run only on a go.
    assert "You never reject it yourself." in wrong
    assert "After the owner's go, re-run that job (`gh run rerun <id> --failed`)" in staging


def test_the_deploy_skill_says_where_it_runs_and_what_to_do_when_staging_misbehaves() -> None:
    skill = _skill_text()
    where = _flat(_section(skill, "## Where to run"))
    wrong = _flat(_section(skill, "## If something goes wrong"))
    steps = _flat(_section(skill, "## Production: steps"))
    # The staging exception names the commands it uses, and "nothing runs on an instance" is the
    # production instance, not this host.
    assert "(and the git and docker commands listed there)" in where
    assert "nothing runs on the production instance from here" in where
    assert "nothing runs on an instance from here" not in where
    # An explicit sha is covered by the same check as the tip.
    assert "none covers that commit" in steps
    assert "none covers the tip" not in steps
    # A slow, hung or rolled-back staging apply is reported, never fixed by killing or re-running.
    assert (
        "if an apply is slow, hangs (for example on a docker pull) or is rolled back by the smoke "
        "check, report the lines verbatim and ask the owner. Do not kill any process and do not "
        "re-run a failed job on your own: a re-run is a rollout action and needs the owner's go. "
        "It may be host load (unproven)."
    ) in wrong
    assert "Host load is the usual cause" not in _flat(_skill_text())


def test_the_deploy_skill_points_at_the_one_entry_runbook_and_the_setup_scripts() -> None:
    flat = _flat(_skill_text())
    for needle in (
        "deploy/DEPLOY.md",
        "deploy/aws/setup-target.sh",
        "deploy/github/setup-environments.sh",
        "deploy-production-auto",
        "gh variable set PROD_AUTO_PLAN --repo amsyrzmi/CSAV2 --body true|false",
        "nothing to plan: ...",
        "no approval today",
    ):
        assert needle in flat, needle
    assert "The owner approves the image publish" not in flat


def test_the_pictures_hold_is_carried_over_unchanged() -> None:
    hold = _flat(_section(_skill_text(), "## Production: hold before step 1"))
    assert hold.endswith(
        "If the target contains the pictures PR 2b commits (the QR picture with payment answers "
        "and Telegram reminders) and production does not yet run `c770ad77` "
        "(`c770ad770eb426ebc93ad923e55fd3f588afc05a`, PR 2a) or a later commit, stop and tell "
        "the owner: production must first deploy `c770ad77` with an explicit `sha` "
        '(`deploy/PRODUCTION.md`, "Pictures (PR 2b): before the first production deploy").'
    )


def test_the_deploy_skill_has_the_planned_sections_in_order() -> None:
    headings = [title for level, title, _ in _headings(_skill_text()) if level <= 2]
    assert headings == [
        "Deploy",
        "Which flow",
        "Where to run",
        "Staging: automatic rollouts",
        "Staging: manual rollout (break-glass)",
        "Production: hold before step 1",
        "Production: steps",
        "Auto-planned production runs",
        "If something goes wrong",
        "Resetting staging",
        "Never",
    ]


def test_deploy_doc_names_the_deploy_skill_without_the_later_in_this_series_phrase() -> None:
    text = _flat(_deploy())
    assert "added later in this series" not in text
    assert "The `deploy` skill:" in text
    assert "`.claude/skills/deploy/SKILL.md`" in text


def test_the_trust_paragraph_describes_the_ruleset_and_what_it_does_not_enforce() -> None:
    deploy = _deploy()
    staging = _flat(_section(deploy, "## Staging: automatic rollouts (deploy-staging)"))
    trust = staging[staging.index("**Trust.**") : staging.index("**Access.**")]
    for needle in (
        "one repository ruleset, `main: pull requests only`",
        "created by the owner on 2026-11-18",
        '"One-time setup", "Protect main"',
        "requires a pull request for every change to the default branch",
        "forbids force pushes and deletion of `main`",
        "has no bypass actor, so no one, an admin included, can push to `main` directly",
        "does not require an approving review (the count is 0)",
        "does not require any status check",
        "a merged commit whose `ci` is green rolls out to staging by itself",
        "The human gate is the click on the production approval",
        "a repository setting that the owner applies",
        "nothing in this repository's code creates, enforces or verifies it",
    ):
        assert needle in trust, needle
    # The old claim is gone everywhere in the runbook.
    assert "required review, no bypass" not in _flat(deploy)
    assert "branch protection (" not in _flat(deploy)
    auto = _flat(_section(deploy, "## Auto-planned production runs (PROD_AUTO_PLAN)"))
    for needle in (
        "Merging to `main` needs a pull request but no review and no green check "
        "(the ruleset `main: pull requests only`",
        "the production approval click is the only human gate between a merged commit and "
        "production",
        "**No agent cancels a production run.**",
        "The only automatic cancel is the workflow's own `supersede`, under the seven conditions "
        'listed in "Cancelling stale waiting runs, and its limits"',
        "An agent never cancels a production run itself, because cancelling a waiting apply is a "
        "reject in effect",
        "`gh run cancel <run id> --repo amsyrzmi/CSAV2`",
        "never one that is approved, queued, `in_progress` or finished",
    ):
        assert needle in auto, needle


def test_the_protect_main_setup_step_records_the_command_the_check_and_the_removal() -> None:
    deploy = _deploy()
    setup = _section(deploy, "### Protect main (once, for the repository)")
    flat = _flat(setup)
    for needle in (
        "the owner created this repository ruleset on 2026-11-18",
        "no script of this repository creates or checks it",
        "gh api --method POST repos/amsyrzmi/CSAV2/rulesets --input - <<'JSON'",
        '"name": "main: pull requests only"',
        '"target": "branch"',
        '"enforcement": "active"',
        '"conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}',
        '"bypass_actors": []',
        '"type": "pull_request"',
        '"required_approving_review_count": 0',
        '"dismiss_stale_reviews_on_push": false',
        '"require_code_owner_review": false',
        '"require_last_push_approval": false',
        '"required_review_thread_resolution": false',
        '{"type": "non_fast_forward"}',
        '{"type": "deletion"}',
        "gh api repos/amsyrzmi/CSAV2/rules/branches/main --jq '.[].type'",
        "expect `pull_request`, `non_fast_forward` and `deletion`",
        "gh api repos/amsyrzmi/CSAV2/rulesets --jq '.[] | [.id,.name] | @tsv'",
        "gh api --method DELETE repos/amsyrzmi/CSAV2/rulesets/<id>",
    ):
        assert needle in flat, needle
    # The JSON body is valid and says what the prose says; no numeric id of the real ruleset.
    body = setup.split("<<'JSON'\n", 1)[1].split("\nJSON", 1)[0]
    rules = json.loads(body)
    assert rules["bypass_actors"] == []
    assert [rule["type"] for rule in rules["rules"]] == [
        "pull_request",
        "non_fast_forward",
        "deletion",
    ]
    assert rules["rules"][0]["parameters"]["required_approving_review_count"] == 0
    assert not re.search(r"rulesets/\d", deploy)


def test_the_hung_pull_outcome_row_gives_the_look_the_owner_step_and_the_honest_cause() -> None:
    section = _section(_deploy(), "## Staging: automatic rollouts (deploy-staging)")
    rows = [line for line in section.splitlines() if "stuck `docker pull`" in line]
    assert len(rows) == 1 and rows[0].startswith("| ")
    row = _flat(rows[0])
    for needle in (
        "ends red with runner exit 124 or `TimedOut`",
        "`timeoutSeconds` is 7200 in `deploy/staging/ssm-document-*.json`",
        "the job limit is 130 minutes",
        "holds the `deploy-staging` concurrency group and the host's rollout lock for up to about "
        "two hours",
        "Nothing is recorded as staged meanwhile, so `deploy-production-auto` reports "
        "`not staged yet`",
        "the apply stops before anything is recreated",
        "`pgrep -fa 'docker pull'`",
        "the `docker pull` child of `csa-staging-deploy`",
        "stops only that stuck `docker pull` client with `sudo kill <pid>`",
        '"Re-run failed jobs" on the run, which is a rollout action that needs the owner\'s go',
        "likely cause but unproven",
    ):
        assert needle in row, needle
    # The facts the row cites are what the files say.
    for document in ("plan", "apply"):
        text = (ROOT / "deploy" / "staging" / f"ssm-document-{document}.json").read_text()
        assert '"timeoutSeconds": "7200"' in text
    workflow = (ROOT / ".github" / "workflows" / "deploy-staging.yml").read_text()
    assert workflow.count("timeout-minutes: 130") == 2


def test_order_and_checks_say_production_needs_two_reviewers_on_an_empty_repository() -> None:
    order = _flat(_section(_deploy(), "### Order and checks"))
    assert (
        "production needs `--reviewer <login-1> --reviewer <login-2>` (the script refuses without "
        "a reviewer)" in order
    )


def test_the_apply_line_grammar_names_rotated_and_changed() -> None:
    setup = _flat(_section(_deploy(), "### The GitHub side: deploy/github/setup-environments.sh"))
    for needle in (
        "`secret <NAME> on <env>: set`, `rotated`, `unchanged` or `missing`",
        "(`set`, `changed`, `unchanged` or `missing`)",
        "`rotated` is a `SETUP_<NAME>` value written over an existing secret",
        "`changed` is a `--region` or `--image-namespace` value written over an existing "
        "variable that held another value",
        "Neither the new nor the old value is ever printed",
    ):
        assert needle in setup, needle
