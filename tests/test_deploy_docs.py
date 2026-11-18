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
        "it is counted as missing",
        "`secret <NAME> on <env>: set`",
        "`apply: <n> item(s) still missing`",
        "`apply: everything is in place`",
        "`variable PROD_AUTO_PLAN: set (true)`",
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
