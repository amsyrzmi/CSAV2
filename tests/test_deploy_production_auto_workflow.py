"""The ``deploy-production-auto`` workflow: its shape is checked as text, its expressions are
evaluated, and its shell blocks are extracted and run against a stub ``auto_plan.sh``.

What it decides (is there anything to plan, which stale runs to cancel, how the dispatch is made
and verified) lives in ``deploy/production/auto_plan.sh`` and is tested in
``tests/test_production_auto_plan.py``. The checks here cover the wiring and the guarantee that
matters most: the auto path has no way to approve, reject or skip the approval on the
``production`` environment, and with the switch off every job is skipped.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.test_deploy_staging_workflow import _code, _evaluate
from tests.test_production_deploy_assets import (
    NEW_FILES,
    PIN,
    PRODUCTION_DIR,
    ROOT,
    SHA,
    Harness,
    _children,
    _jobs,
    _run_blocks,
    _run_step,
    _step,
    _steps,
    _unquoted_expansions,
    _workflow,
)
from tests.test_production_deploy_assets import WORKFLOW as PRODUCTION_WORKFLOW

AUTO_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-production-auto.yml"
AUTO_PLAN = PRODUCTION_DIR / "auto_plan.sh"
MAIN = "github.ref == 'refs/heads/main'"
PROCEED = "if: steps.gate.outputs.proceed == 'true'"
GATE_STEP = "Decide whether there is anything to plan"
SUPERSEDE_STEP = "Cancel earlier auto-started runs that are still waiting"
START_STEP = "Start the production plan"
START_SENTENCE = "The plan starts now; the apply waits for approval on the production environment."
GROUP = (
    "${{ github.event.workflow_run.conclusion == 'success' "
    "&& github.event.workflow_run.head_branch == 'main' "
    "&& github.event.workflow_run.head_repository.full_name == github.repository "
    "&& vars.PROD_AUTO_PLAN == 'true' "
    "&& 'deploy-production-auto' || format('deploy-production-auto-idle-{0}', github.run_id) }}"
)
JOB_IF = (
    f"if: {MAIN} && github.event.workflow_run.conclusion == 'success' "
    "&& github.event.workflow_run.head_branch == 'main' "
    "&& github.event.workflow_run.head_repository.full_name == github.repository "
    "&& vars.PROD_AUTO_PLAN == 'true'"
)
JOB_ENV = [
    "GH_TOKEN: ${{ github.token }}",
    "REPOSITORY: ${{ github.repository }}",
    "IMAGE_NAMESPACE: ${{ vars.IMAGE_NAMESPACE }}",
    "REGISTRY_USER: ${{ github.actor }}",
    "REGISTRY_TOKEN: ${{ github.token }}",
]
JOB_PERMISSIONS = ["actions: write", "contents: read", "deployments: read", "packages: read"]


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def _text() -> str:
    return _workflow(AUTO_WORKFLOW)


def _auto_step(name: str) -> str:
    return _step("plan", name, AUTO_WORKFLOW)


def _auto_run(name: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return _run_step("plan", name, env, cwd, AUTO_WORKFLOW)


def _job_children(header: str) -> list[str]:
    return _children("  job:\n" + _jobs(AUTO_WORKFLOW)["plan"], header)


def _job_if() -> str:
    (line,) = [
        line.strip()
        for line in _jobs(AUTO_WORKFLOW)["plan"].splitlines()
        if line.startswith("    if: ")
    ]
    return line


def _context(
    *,
    ref: str = "refs/heads/main",
    conclusion: str = "success",
    branch: str = "main",
    repository: str = "example-owner/CSAV2",
    switch: str = "true",
) -> dict[str, str]:
    return {
        "github.ref": ref,
        "github.event.workflow_run.conclusion": conclusion,
        "github.event.workflow_run.head_branch": branch,
        "github.event.workflow_run.head_repository.full_name": repository,
        "github.repository": "example-owner/CSAV2",
        "github.run_id": "4242",
        "vars.PROD_AUTO_PLAN": switch,
    }


# --- shape ---------------------------------------------------------------------------------------


def test_triggers_permissions_concurrency_and_pins() -> None:
    text = _text()
    top = [line for line in text.splitlines() if line and not line[0].isspace() and line[0] != "#"]
    assert top == ["name: deploy-production-auto", "on:", "permissions:", "concurrency:", "jobs:"]
    # Only the completion of deploy-staging on main starts it: no dispatch, no schedule.
    assert _children(text, "on:") == ["workflow_run:"]
    assert _children(text, "  workflow_run:") == [
        "workflows: [deploy-staging]",
        "types: [completed]",
        "branches: [main]",
    ]
    assert _children(text, "permissions:") == ["contents: read"]
    # The idle group: a run that will not plan anything (the staging run did not succeed, or the
    # switch is off) gets a group of its own, so it cannot replace a pending real run.
    assert _children(text, "concurrency:") == [f"group: {GROUP}", "cancel-in-progress: false"]
    code = _code(text)
    assert len([line for line in code if "concurrency:" in line]) == 1

    jobs = _jobs(AUTO_WORKFLOW)
    assert list(jobs) == ["plan"]
    assert _job_children("    permissions:") == JOB_PERMISSIONS
    assert len([line for line in code if "permissions:" in line]) == 2
    assert "    runs-on: ubuntu-24.04\n" in jobs["plan"]
    assert "    timeout-minutes: 10\n" in jobs["plan"]
    assert _job_children("    env:") == JOB_ENV

    uses = [line for line in code if re.match(r"^\s*(?:- )?uses:", line)]
    assert len(uses) == 1
    match = PIN.match(uses[0])
    assert match and match.group(1) == "actions/checkout"
    # Exactly the checkout pin the deploy-production workflow uses (same commit and comment).
    production_uses = {
        line.strip()
        for line in _workflow(PRODUCTION_WORKFLOW).splitlines()
        if re.match(r"^\s*(?:- )?uses: actions/checkout@", line)
    }
    assert len(production_uses) == 1
    assert uses[0].strip().removeprefix("- ") == next(iter(production_uses)).removeprefix("- ")


def test_the_job_runs_only_for_main_and_only_when_the_switch_is_true() -> None:
    assert _job_if() == JOB_IF
    # The switch is `== 'true'` and nothing else: no `!=`, so unset means off. (GitHub compares
    # strings without regard to letter case, so `TRUE` also switches it on; the shared evaluator
    # here is case-sensitive, so that case is not asserted either way.)
    assert "vars.PROD_AUTO_PLAN == 'true'" in JOB_IF
    assert "!=" not in _job_if()
    assert "needs:" not in _jobs(AUTO_WORKFLOW)["plan"]

    expression = _job_if().removeprefix("if: ")
    cases = [
        (_context(), True),
        (_context(switch=""), False),  # unset
        (_context(switch="false"), False),
        (_context(switch="1"), False),
        (_context(conclusion="failure"), False),
        (_context(conclusion="cancelled"), False),
        (_context(conclusion="skipped"), False),
        (_context(branch="feature"), False),
        (_context(repository="someone-else/CSAV2"), False),
        (_context(ref="refs/heads/feature"), False),
    ]
    for context, runs in cases:
        assert _evaluate(expression, **context) == runs, context


def test_the_concurrency_group_queues_real_runs_and_isolates_everything_else() -> None:
    expression = GROUP.removeprefix("${{ ").removesuffix(" }}")
    idle = "deploy-production-auto-idle-4242"
    cases = [
        (_context(), "deploy-production-auto"),
        (_context(switch=""), idle),
        (_context(switch="false"), idle),
        (_context(conclusion="failure"), idle),
        (_context(conclusion="cancelled"), idle),
        (_context(conclusion="skipped"), idle),
        # The same two clauses as the job's `if`: a run that fails them must not share the real
        # group and replace a pending real run.
        (_context(branch="feature"), idle),
        (_context(repository="someone-else/CSAV2"), idle),
    ]
    for context, group in cases:
        assert _evaluate(expression, **context) == group, context
    # An idle group is per run, never shared with another idle run or with the real group.
    other = {**_context(switch="false"), "github.run_id": "4243"}
    assert _evaluate(expression, **other) == "deploy-production-auto-idle-4243"


def test_the_auto_path_has_no_way_to_approve() -> None:
    text = _text()
    code = "\n".join(_code(text))
    assert not re.search(r"^\s+environment:", code, flags=re.MULTILINE)
    assert not re.search(r"\benvironment\s*:", code)
    assert "id-token" not in code
    assert "secrets" not in code  # not even GITHUB_TOKEN: the token is `github.token`
    assert "configure-aws-credentials" not in code
    assert "download-artifact" not in code
    assert not re.search(r"\binputs\.", code)

    forbidden = (
        "pending_deployments",
        "run approve",
        "run review",
        "gh run",
        "deployments/",
    )
    # Ways to approve, reject or dispatch that do not go through the script: a raw API call in a
    # run line of the workflow itself.
    workflow_forbidden = (
        "/approve",
        "/reject",
        "curl",
        "api.github.com",
        "--input",
        "dispatches",
    )
    for word in workflow_forbidden:
        assert word not in code, word
    arguments = re.compile(r"(?:^|\s)(?:-f|-F|--field|--raw-field)(?:[\s=]|$)")
    script_code = "\n".join(_code(AUTO_PLAN.read_text(encoding="utf-8")))
    for scope in (code, script_code):
        for word in forbidden:
            assert word not in scope, word
        assert not arguments.search(scope)
    # The one dispatch the path makes carries no input at all (a continued line: its tail is `\`).
    (dispatch,) = re.findall(r"gh workflow run [^\n]*", script_code)
    assert dispatch == 'gh workflow run deploy-production.yml --repo "$REPOSITORY" --ref main \\'
    assert "/approve" not in script_code
    assert "pending_deployments" not in text

    # The same job permissions as the plan: no id-token and no write but Actions.
    assert [line for line in _job_children("    permissions:") if "write" in line] == [
        "actions: write"
    ]


def test_no_run_text_interpolates_inputs_or_event_fields_and_head_sha_appears_nowhere() -> None:
    text = _text()
    # The triggering run's own sha is not the commit it handled: never read, not in a comment.
    assert "head_sha" not in text
    blocks = _run_blocks(text)
    assert len(blocks) == 3
    for block in blocks:
        assert block.startswith("set -euo pipefail\n"), block
        assert "${{" not in block, block
        assert "github.event" not in block, block
        assert "inputs." not in block, block
        assert _unquoted_expansions(block) == [], block
    for line in _code(text):
        stripped = line.strip()
        # Event fields appear only in conditions and the concurrency group.
        if "github.event." in line:
            assert stripped.startswith(("if: ", "group: ")), line
            fields = set(re.findall(r"github\.event\.workflow_run\.([a-z_.]+)", line))
            assert fields <= {"conclusion", "head_branch", "head_repository.full_name"}, line


def test_every_step_after_the_gate_waits_for_proceed_true() -> None:
    steps = _steps("plan", AUTO_WORKFLOW)
    names = [step.split("\n", 1)[0] for step in steps]
    assert steps[0].startswith("uses: actions/checkout@")
    assert names[1:] == [
        f"name: {GATE_STEP}",
        f"name: {SUPERSEDE_STEP}",
        f"name: {START_STEP}",
    ]
    gate = steps[1]
    assert "id: gate\n" in gate
    assert "if:" not in gate
    assert "env:" not in gate
    # Every step after the gate waits for its verdict (and so does nothing on `proceed=false`).
    for step in steps[2:]:
        assert re.search(rf"^        {re.escape(PROCEED)}$", step, flags=re.MULTILINE), step
    assert _children("      - " + steps[2], "        env:") == [
        "SHA: ${{ steps.gate.outputs.sha }}"
    ]
    assert "env:" not in steps[3]
    # `proceed` is the only condition: nothing else decides whether the dispatch is made.
    assert [line.strip() for line in _code(_text()) if "steps.gate.outputs.proceed" in line] == [
        PROCEED.removeprefix("- ")
    ] * 2


def _github_output(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            key, _, value = line.partition("=")
            values[key] = value
    return values


def _auto_plan_stub(harness: Harness) -> Path:
    work = harness.root / "work"
    scripts = work / "deploy" / "production"
    scripts.mkdir(parents=True)
    harness._write_stub(
        scripts / "auto_plan.sh",
        '#!/bin/bash\nIFS=$\'\\t\'\nprintf "auto_plan\\t%s\\n" "$*" >> "$STUB_LOG"\n'
        'printf "%s" "$STUB_OUTPUT"\nexit "${STUB_STATUS:-0}"\n',
    )
    return work


def test_the_gate_step_writes_the_outputs_and_the_reason_and_fails_closed(
    harness: Harness,
) -> None:
    work = _auto_plan_stub(harness)
    output = harness.root / "github-output"
    summary = harness.root / "summary.md"
    reason = "nothing to plan: main's tip 0123456 is not staged yet"
    env = harness.env(
        RUNNER_TEMP=str(harness.root),
        GITHUB_OUTPUT=str(output),
        GITHUB_STEP_SUMMARY=str(summary),
        STUB_OUTPUT=f"sha={SHA}\nproceed=false\nreason={reason}\n",
    )
    result = _auto_run(GATE_STEP, env, work)

    assert result.returncode == 0, result.stderr
    assert harness.calls() == [["auto_plan", "check"]]
    assert output.read_text() == f"sha={SHA}\nproceed=false\nreason={reason}\n"
    assert reason in summary.read_text()
    assert reason in result.stdout.decode()
    # The later steps' condition, evaluated on what the step wrote: false.
    condition = PROCEED.removeprefix("if: ")
    outputs = _github_output(output)
    assert _evaluate(condition, **{"steps.gate.outputs.proceed": outputs["proceed"]}) is False

    # A plan to make: proceed=true, and the sha the later steps use.
    output.unlink()
    env = harness.env(
        RUNNER_TEMP=str(harness.root),
        GITHUB_OUTPUT=str(output),
        GITHUB_STEP_SUMMARY=str(summary),
        STUB_OUTPUT=f"sha={SHA}\nproceed=true\nreason=planning 0123456\n",
    )
    assert _auto_run(GATE_STEP, env, work).returncode == 0
    outputs = _github_output(output)
    assert outputs == {"sha": SHA, "proceed": "true", "reason": "planning 0123456"}
    assert _evaluate(condition, **{"steps.gate.outputs.proceed": outputs["proceed"]}) is True

    # A failing check fails the step and leaves the output file without a verdict.
    output.unlink()
    env = harness.env(
        RUNNER_TEMP=str(harness.root),
        GITHUB_OUTPUT=str(output),
        GITHUB_STEP_SUMMARY=str(summary),
        STUB_OUTPUT="proceed=true\n",
        STUB_STATUS="1",
    )
    assert _auto_run(GATE_STEP, env, work).returncode == 1
    assert not output.exists() or output.read_text() == ""


def test_supersede_gets_the_sha_the_gate_reported_and_start_writes_no_output(
    harness: Harness,
) -> None:
    work = _auto_plan_stub(harness)
    output = harness.root / "github-output"
    summary = harness.root / "summary.md"
    cancelled = f"cancelled run 12 (planned {'f' * 7})"
    env = harness.env(
        RUNNER_TEMP=str(harness.root),
        GITHUB_OUTPUT=str(output),
        GITHUB_STEP_SUMMARY=str(summary),
        SHA=SHA,
        STUB_OUTPUT=cancelled + "\n",
    )
    result = _auto_run(SUPERSEDE_STEP, env, work)
    assert result.returncode == 0, result.stderr
    assert harness.calls() == [["auto_plan", "supersede", SHA]]
    assert cancelled in result.stdout.decode()

    # `started deploy-production run <id>: <url>` is not key=value: it goes to the log and the
    # summary and never to $GITHUB_OUTPUT.
    started = (
        "started deploy-production run 77: https://github.com/example-owner/CSAV2/actions/runs/77"
    )
    harness.log.unlink()
    env = harness.env(
        RUNNER_TEMP=str(harness.root),
        GITHUB_OUTPUT=str(output),
        GITHUB_STEP_SUMMARY=str(summary),
        STUB_OUTPUT=started + "\n",
    )
    result = _auto_run(START_STEP, env, work)
    assert result.returncode == 0, result.stderr
    assert harness.calls() == [["auto_plan", "start"]]
    assert not output.exists()
    assert started in result.stdout.decode()
    assert summary.read_text() == f"{started}\n\n{START_SENTENCE}\n"

    # A failed start (no run appeared, a refused dispatch) fails the step and the summary does
    # not claim the plan starts.
    summary.unlink()
    env = harness.env(
        RUNNER_TEMP=str(harness.root),
        GITHUB_OUTPUT=str(output),
        GITHUB_STEP_SUMMARY=str(summary),
        STUB_OUTPUT="",
        STUB_STATUS="1",
    )
    assert _auto_run(START_STEP, env, work).returncode == 1
    assert not summary.exists() or START_SENTENCE not in summary.read_text()
    assert not output.exists()


def test_scripts_come_from_a_main_checkout() -> None:
    text = _text()
    assert re.findall(r"^[ \t]*ref:.*$", text, flags=re.MULTILINE) == ["          ref: main"]
    assert "working-directory:" not in text

    steps = _steps("plan", AUTO_WORKFLOW)
    (checkout,) = [i for i, step in enumerate(steps) if "uses: actions/checkout@" in step]
    assert checkout == 0
    assert _children("      - " + steps[checkout], "        with:") == [
        "ref: main",
        "persist-credentials: false",
    ]
    invoked = re.findall(r"deploy/\w+/\w+\.sh[^\n>|]*", "".join(steps))
    assert invoked == [
        "deploy/production/auto_plan.sh check ",
        'deploy/production/auto_plan.sh supersede "$SHA"',
        "deploy/production/auto_plan.sh start ",
    ]
    # Nothing from the triggering run is used: no artifact, no other checkout ref.
    assert "artifact" not in "\n".join(_code(text)).lower()


def test_the_header_names_the_switch_the_approval_and_the_chain() -> None:
    header = _text().split("\nname: deploy-production-auto\n", 1)[0]
    flat = " ".join(re.sub(r"^\s*#+ ?", "", line) for line in header.splitlines())
    flat = " ".join(flat.split())
    for needle in (
        "PROD_AUTO_PLAN",
        "must be `true`",
        "off by default",
        "unset, or anything that is not `true`, letter case ignored",
        "review request",
        "PLAN only",
        "waits for approval on the `production` environment",
        "nothing here can give that approval",
        "three `workflow_run` links",
        "the tip of `main`",
        "never reads the sha of the event",
        "concludes `skipped`",
        "deploy-staging",
    ):
        assert needle in flat, needle
    # A workflow that is `workflow_run`-started on a run that did not succeed does nothing.
    assert "did not succeed" in flat


def test_the_workflow_holds_no_account_instance_region_or_address() -> None:
    # The scan of the other deploy files (a parametrised test of the production assets) also
    # covers this workflow and the two scripts the setup and the auto path add.
    for path in (
        AUTO_WORKFLOW,
        AUTO_PLAN,
        ROOT / "deploy" / "github" / "setup-environments.sh",
    ):
        assert path in NEW_FILES, path
    text = _text()
    assert not re.search(r"\b\d{12}\b", text)
    assert not re.search(r"\b(?:mi|i)-[0-9a-f]{8,17}\b", text)
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)
    assert not re.search(r"\b(?:us|eu|ap|sa|ca|me|af|il|mx)-[a-z]+-\d\b", text)
    assert not re.search(r"arn:aws:iam::\d", text)


def test_deploy_production_is_untouched_by_this_pr() -> None:
    """The facts the auto path depends on, so that editing deploy-production.yml without
    noticing fails here (not a hash: the file may change for its own reasons)."""
    text = _workflow(PRODUCTION_WORKFLOW)
    assert text.startswith("# Deploy one commit on main to production")
    assert "Manual\n# dispatch only." in text
    assert _children(text, "on:") == ["workflow_dispatch:"]
    assert _children(text, "    inputs:") == ["sha:"]
    sha_input = _children(text, "      sha:")
    assert 'default: ""' in sha_input
    assert "required: false" in sha_input
    assert _children(text, "concurrency:") == [
        "group: deploy-production",
        "cancel-in-progress: false",
    ]
    jobs = _jobs(PRODUCTION_WORKFLOW)
    assert list(jobs) == ["plan", "apply"]
    assert "    environment: production-plan\n" in jobs["plan"]
    assert "    environment: production\n" in jobs["apply"]
    # The script the auto path calls dispatches this very file.
    assert "deploy-production.yml" in AUTO_PLAN.read_text(encoding="utf-8")
