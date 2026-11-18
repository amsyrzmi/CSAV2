"""The ``deploy-staging`` workflow: its shape is checked as text, its expressions are evaluated, and
its shell blocks are extracted and run against stub scripts (the helpers come from the
``deploy-production`` workflow tests, which read the same kind of file).

The decisions themselves (is the tip ready, is it already staged, has main moved) live in
``deploy/staging/rollout_gate.sh`` and are tested in ``tests/test_staging_gate_scripts.py``; the
checks here cover the wiring: which job runs when, in what order, and with which environment.
"""

from __future__ import annotations

import collections
import re
import subprocess
from pathlib import Path

import pytest

from tests.test_production_deploy_assets import (
    FE_DIGEST,
    PIN,
    PY_DIGEST,
    ROOT,
    SHA,
    TIP,
    Harness,
    _children,
    _jobs,
    _outputs,
    _plan_output,
    _run_blocks,
    _run_step,
    _step,
    _steps,
    _unbracketed,
    _unquoted_expansions,
    _workflow,
    _workspace,
)
from tests.test_production_deploy_assets import WORKFLOW as PRODUCTION_WORKFLOW

STAGING_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-staging.yml"
GATE = ROOT / "deploy" / "staging" / "rollout_gate.sh"
MAIN = "github.ref == 'refs/heads/main'"
PROCEED = "if: steps.gate.outputs.proceed == 'true'"
CURRENT = "if: steps.current.outputs.current == 'true'"
PAUSED_MESSAGE = (
    "Automatic staging rollouts are paused (repository variable STAGING_AUTO_PAUSED). "
    "Nothing was sent. Run deploy-staging by hand, or set the variable to false."
)
GROUP = (
    "${{ github.ref == 'refs/heads/main' && (github.event_name == 'workflow_dispatch' || "
    "(github.event.workflow_run.conclusion == 'success' && vars.STAGING_AUTO_PAUSED != 'true')) "
    "&& 'deploy-staging' || format('deploy-staging-idle-{0}', github.run_id) }}"
)
PLAN_IF = (
    f"if: {MAIN} && (github.event_name == 'workflow_dispatch' || "
    "(github.event_name == 'workflow_run' && github.event.workflow_run.conclusion == 'success' "
    "&& github.event.workflow_run.head_branch == 'main' "
    "&& github.event.workflow_run.head_repository.full_name == github.repository "
    "&& vars.STAGING_AUTO_PAUSED != 'true'))"
)
PAUSED_IF = (
    f"if: {MAIN} && github.event_name == 'workflow_run' "
    "&& github.event.workflow_run.conclusion == 'success' && vars.STAGING_AUTO_PAUSED == 'true'"
)
JOB_PERMISSIONS = {
    "plan": [
        "actions: read",
        "contents: read",
        "deployments: read",
        "id-token: write",
        "packages: read",
    ],
    "apply": ["contents: read", "id-token: write"],
    "record": ["contents: read", "deployments: write"],
}


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def _text() -> str:
    return _workflow(STAGING_WORKFLOW)


def _staging_jobs() -> dict[str, str]:
    return _jobs(STAGING_WORKFLOW)


def _staging_steps(job: str) -> list[str]:
    return _steps(job, STAGING_WORKFLOW)


def _staging_step(job: str, name: str) -> str:
    return _step(job, name, STAGING_WORKFLOW)


def _staging_run(
    job: str, name: str, env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess[bytes]:
    return _run_step(job, name, env, cwd, STAGING_WORKFLOW)


def _job_children(job: str, header: str) -> list[str]:
    return _children("  job:\n" + _staging_jobs()[job], header)


def _code(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def _evaluate(expression: str, **context: str) -> object:
    """Evaluate a workflow expression made of ``&&``, ``||``, comparisons and ``format()``."""
    values: dict[str, str] = collections.defaultdict(str, context)
    python = expression.replace("&&", " and ").replace("||", " or ")
    python = re.sub(
        r"\b[A-Za-z_][A-Za-z_]*(?:\.[A-Za-z_]+)+\b", lambda m: f"ctx[{m.group(0)!r}]", python
    )
    names = {"ctx": values, "format": lambda text, *args: text.format(*args)}
    return eval(python, {"__builtins__": {}}, names)  # noqa: S307 - our own workflow text


def _context(
    *,
    event: str = "workflow_run",
    ref: str = "refs/heads/main",
    conclusion: str = "success",
    branch: str = "main",
    repository: str = "example-owner/CSAV2",
    paused: str = "",
) -> dict[str, str]:
    return {
        "github.ref": ref,
        "github.event_name": event,
        "github.event.workflow_run.conclusion": conclusion,
        "github.event.workflow_run.head_branch": branch,
        "github.event.workflow_run.head_repository.full_name": repository,
        "github.repository": "example-owner/CSAV2",
        "github.run_id": "4242",
        "vars.STAGING_AUTO_PAUSED": paused,
    }


def _job_if(job: str) -> str:
    (line,) = [
        line.strip() for line in _staging_jobs()[job].splitlines() if line.startswith("    if: ")
    ]
    return line


# --- shape ---------------------------------------------------------------------------------------


def test_triggers_permissions_concurrency_and_pins() -> None:
    text = _text()
    top = [line for line in text.splitlines() if line and not line[0].isspace() and line[0] != "#"]
    assert top == ["name: deploy-staging", "on:", "permissions:", "concurrency:", "jobs:"]
    assert _children(text, "on:") == ["workflow_run:", "workflow_dispatch:"]
    assert _children(text, "  workflow_run:") == [
        "workflows: [publish-staging-images]",
        "types: [completed]",
        "branches: [main]",
    ]
    assert _children(text, "    inputs:") == ["sha:"]
    sha_input = _children(text, "      sha:")
    assert "required: false" in sha_input
    assert "type: string" in sha_input
    (description,) = [line for line in sha_input if line.startswith("description:")]
    assert description == 'description: "Full commit SHA on main (empty = the tip of main)"'
    assert _children(text, "permissions:") == ["contents: read"]
    assert _children(text, "concurrency:") == [f"group: {GROUP}", "cancel-in-progress: false"]
    code = _code(text)
    assert len([line for line in code if "concurrency:" in line]) == 1

    jobs = _staging_jobs()
    assert list(jobs) == ["paused", "plan", "apply", "record"]
    # The paused job's empty permissions map has no children, so it is checked on its own.
    assert "    permissions: {}\n" in jobs["paused"]
    assert len([line for line in code if "permissions:" in line]) == 5
    for name, permissions in JOB_PERMISSIONS.items():
        assert _job_children(name, "    permissions:") == permissions, name
    for name, body in jobs.items():
        assert "    runs-on: ubuntu-24.04\n" in body, name

    uses = [line for line in _code(text) if re.match(r"^\s*(?:- )?uses:", line)]
    assert len(uses) == 5
    pinned = [PIN.match(line) for line in uses]
    assert all(pinned), uses
    assert sorted({match.group(1) for match in pinned if match}) == [
        "actions/checkout",
        "aws-actions/configure-aws-credentials",
    ]
    # Exactly the two pins deploy-production uses (same commits and version comments).
    production_uses = {
        line.strip()
        for line in _workflow(PRODUCTION_WORKFLOW).splitlines()
        if re.match(r"^\s*(?:- )?uses:", line)
    }
    assert len(production_uses) == 2
    assert {line.strip().removeprefix("- ") for line in uses} == {
        line.removeprefix("- ") for line in production_uses
    }


def test_plan_and_apply_use_staging_without_a_deployment_and_record_names_no_environment() -> None:
    jobs = _staging_jobs()
    for name in ("plan", "apply"):
        assert _job_children(name, "    environment:") == ["name: staging", "deployment: false"]
        assert "    timeout-minutes: 130\n" in jobs[name]
        assert _job_children(name, "    env:") == [
            "ROLE_ARN: ${{ secrets.STAGING_DEPLOY_ROLE_ARN }}",
            "AWS_REGION: ${{ vars.STAGING_DEPLOY_REGION }}",
            "INSTANCE_ID: ${{ secrets.STAGING_INSTANCE_ID }}",
            "DEPLOY_TARGET: staging",
        ]
    for name in ("paused", "record"):
        assert "environment" not in jobs[name], name
    code = "\n".join(_code(_text()))
    assert len(re.findall(r"^\s+environment:", code, flags=re.MULTILINE)) == 2
    assert code.count("deployment: false") == 2


def test_every_job_runs_only_for_main_and_plan_honours_the_pause_only_for_workflow_run() -> None:
    assert _job_if("paused") == PAUSED_IF
    assert _job_if("plan") == PLAN_IF
    assert _job_if("apply") == f"if: {MAIN} && needs.plan.outputs.proceed == 'true'"
    assert _job_if("record") == f"if: {MAIN} && needs.apply.outputs.applied == 'true'"
    jobs = _staging_jobs()
    assert "    needs: plan\n" in jobs["apply"]
    assert "    needs: [plan, apply]\n" in jobs["record"]
    for name in ("paused", "plan"):
        assert "needs:" not in jobs[name]

    # Evaluate the two expressions that decide on the pause, over every case that matters.
    plan_if = PLAN_IF.removeprefix("if: ")
    paused_if = PAUSED_IF.removeprefix("if: ")
    cases = [
        # (context, runs plan, shows the paused notice)
        (_context(), True, False),
        (_context(paused="false"), True, False),
        (_context(paused="true"), False, True),
        (_context(conclusion="failure"), False, False),
        (_context(conclusion="failure", paused="true"), False, False),
        (_context(conclusion="skipped"), False, False),
        (_context(branch="feature"), False, False),
        (_context(repository="someone-else/CSAV2"), False, False),
        (_context(ref="refs/heads/feature"), False, False),
        (_context(event="workflow_dispatch"), True, False),
        (_context(event="workflow_dispatch", conclusion="", paused="true"), True, False),
        (_context(event="workflow_dispatch", ref="refs/heads/feature"), False, False),
    ]
    for context, plans, notice in cases:
        assert _evaluate(plan_if, **context) == plans, context
        assert _evaluate(paused_if, **context) == notice, context
    # Never both: a paused automatic run only says so.
    for context, plans, notice in cases:
        assert not (plans and notice), context


def test_the_concurrency_group_queues_real_runs_and_isolates_everything_else() -> None:
    expression = GROUP.removeprefix("${{ ").removesuffix(" }}")
    idle = "deploy-staging-idle-4242"
    cases = [
        (_context(), "deploy-staging"),
        (_context(paused="false"), "deploy-staging"),
        (_context(event="workflow_dispatch", conclusion="", paused="true"), "deploy-staging"),
        (_context(paused="true"), idle),
        (_context(conclusion="failure"), idle),
        (_context(conclusion="skipped"), idle),
        (_context(ref="refs/heads/feature"), idle),
        (_context(event="workflow_dispatch", ref="refs/heads/feature"), idle),
    ]
    for context, group in cases:
        assert _evaluate(expression, **context) == group, context


def test_no_run_text_interpolates_inputs_or_event_fields() -> None:
    text = _text()
    # The publish run's own head sha is not the commit it published (the plan's first decision):
    # the workflow never reads it, not even in a comment.
    assert "head_sha" not in text
    blocks = _run_blocks(text)
    assert len(blocks) == 10
    for block in blocks:
        assert block.startswith("set -euo pipefail\n"), block
        assert "${{" not in block, block
        assert _unquoted_expansions(block) == [], block
    for line in _code(text):
        stripped = line.strip()
        # Secrets reach a step only through env: or an action's with: input.
        if "secrets." in line:
            assert re.fullmatch(
                r"(?:[A-Z][A-Z_]*|role-to-assume): \$\{\{ secrets\.[A-Z_]+ \}\}", stripped
            ), line
        # Event fields appear only in conditions, the concurrency group and EVENT_NAME.
        if "github.event." in line:
            assert stripped.startswith(("if: ", "group: ")), line
        if "inputs." in line:
            assert stripped == "INPUT_SHA: ${{ inputs.sha }}", line
    outputs = {
        "proceed: ${{ steps.gate.outputs.proceed }}",
        "sha: ${{ steps.parse.outputs.sha }}",
        "explicit: ${{ steps.gate.outputs.explicit }}",
        "migrations: ${{ steps.parse.outputs.migrations }}",
        "python_digest: ${{ steps.gate.outputs.python_digest }}",
        "frontend_digest: ${{ steps.gate.outputs.frontend_digest }}",
    }
    assert set(_job_children("plan", "    outputs:")) == outputs
    assert _job_children("apply", "    outputs:") == ["applied: ${{ steps.done.outputs.applied }}"]
    for name in ("paused", "record"):
        assert "outputs:" not in _staging_jobs()[name]


def test_the_header_names_the_secrets_the_pause_and_newest_wins() -> None:
    header = _text().split("\nname: deploy-staging\n", 1)[0]
    for needle in (
        "STAGING_AUTO_PAUSED",
        "environment secrets",
        "STAGING_DEPLOY_ROLE_ARN",
        "newest",
        "nothing to roll out",
        "superseded",
        "already staged",
    ):
        assert needle in header, needle


# --- the plan job --------------------------------------------------------------------------------


def _variables_env(**overrides: str | None) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "ROLE_ARN": "arn:aws:iam::<account-id>:role/example",
        "AWS_REGION": "test-region-1",
        "INSTANCE_ID": "mi-0123456789abcdef0",
    }
    for name, value in overrides.items():
        if value is None:
            env.pop(name)
        else:
            env[name] = value
    return env


@pytest.mark.parametrize("job", ["plan", "apply"])
def test_missing_variables_fail_before_the_aws_step(job: str, tmp_path: Path) -> None:
    steps = _staging_steps(job)
    names = [step.split("\n", 1)[0] for step in steps]
    (configure,) = [i for i, step in enumerate(steps) if "configure-aws-credentials@" in step]
    require = names.index("name: Require the deploy variables")
    assert require < configure
    step = steps[configure]
    assert "role-to-assume: ${{ secrets.STAGING_DEPLOY_ROLE_ARN }}" in step
    assert "aws-region: ${{ vars.STAGING_DEPLOY_REGION }}" in step
    assert "mask-aws-account-id: true" in step
    assert f"role-session-name: deploy-staging-{job}\n" in step
    assert "PROD_" not in _text()

    name = "Require the deploy variables"
    ok = _run_step(job, name, _variables_env(), tmp_path, STAGING_WORKFLOW)
    assert ok.returncode == 0, ok.stderr
    settings = [
        ("ROLE_ARN", "STAGING_DEPLOY_ROLE_ARN"),
        ("AWS_REGION", "STAGING_DEPLOY_REGION"),
        ("INSTANCE_ID", "STAGING_INSTANCE_ID"),
    ]
    for variable, setting in settings:
        for value in ("", None):
            result = _run_step(
                job, name, _variables_env(**{variable: value}), tmp_path, STAGING_WORKFLOW
            )
            assert result.returncode != 0
            assert setting.encode() in result.stderr
            assert b"staging" in result.stderr


def test_skip_reasons_end_the_plan_job_green_and_skip_every_later_step() -> None:
    steps = _staging_steps("plan")
    names = [step.split("\n", 1)[0] for step in steps]
    assert steps[0].startswith("uses: actions/checkout@")
    assert steps[3].startswith("uses: aws-actions/configure-aws-credentials@")
    assert [names[i] for i in (1, 2, 4, 5)] == [
        "name: Decide what to roll out",
        "name: Require the deploy variables",
        "name: Plan on the staging node",
        "name: Read the plan",
    ]
    assert len(names) == 6
    gate = steps[1]
    assert "id: gate\n" in gate
    assert "if:" not in gate
    assert _children("      - " + gate, "        env:") == [
        "EVENT_NAME: ${{ github.event_name }}",
        "INPUT_SHA: ${{ inputs.sha }}",
        "GH_TOKEN: ${{ github.token }}",
        "REPOSITORY: ${{ github.repository }}",
        "IMAGE_NAMESPACE: ${{ vars.IMAGE_NAMESPACE }}",
        "REGISTRY_USER: ${{ github.actor }}",
        "REGISTRY_TOKEN: ${{ github.token }}",
    ]
    # Every step after the gate waits for its verdict (and so does nothing on a skip).
    for step in steps[2:]:
        assert re.search(rf"^        {re.escape(PROCEED)}$", step, flags=re.MULTILINE), step
    assert "id: parse\n" in steps[5]


def test_the_gate_step_writes_the_outputs_and_the_reason_and_fails_closed(
    harness: Harness,
) -> None:
    work = harness.root / "work"
    scripts = work / "deploy" / "staging"
    scripts.mkdir(parents=True)
    harness._write_stub(
        scripts / "rollout_gate.sh",
        '#!/bin/bash\nprintf "gate\\t%s\\n" "$*" >> "$STUB_LOG"\n'
        'printf "%s" "$STUB_GATE_OUTPUT"\nexit "${STUB_GATE_STATUS:-0}"\n',
    )
    output = harness.root / "github-output"
    summary = harness.root / "summary.md"
    reason = "nothing to roll out: images are not published for this commit yet"
    env = harness.env(
        RUNNER_TEMP=str(harness.root),
        GITHUB_OUTPUT=str(output),
        GITHUB_STEP_SUMMARY=str(summary),
        STUB_GATE_OUTPUT=f"proceed=false\nreason={reason}\n",
    )
    result = _staging_run("plan", "Decide what to roll out", env, work)

    assert result.returncode == 0, result.stderr
    assert harness.calls() == [["gate", "start"]]
    assert output.read_text() == f"proceed=false\nreason={reason}\n"
    assert reason in summary.read_text()

    # A refusal fails the step and leaves the output file without a verdict.
    output.unlink()
    summary.unlink()
    env = harness.env(
        RUNNER_TEMP=str(harness.root),
        GITHUB_OUTPUT=str(output),
        GITHUB_STEP_SUMMARY=str(summary),
        STUB_GATE_OUTPUT="proceed=true\n",
        STUB_GATE_STATUS="1",
    )
    assert _staging_run("plan", "Decide what to roll out", env, work).returncode == 1
    assert not output.exists() or output.read_text() == ""


def test_the_plan_step_sends_the_plan_summarises_it_and_the_values_reach_the_parse_step(
    harness: Harness,
) -> None:
    work = _workspace(harness)
    plan = _plan_output(["0486", "0487"], production=False)
    summary = harness.root / "summary.md"
    output = harness.root / "github-output"
    job_env = _job_children("plan", "    env:")
    (target,) = [line.split(": ", 1)[1] for line in job_env if line.startswith("DEPLOY_TARGET: ")]
    assert target == "staging"
    env = harness.env(
        DEPLOY_TARGET=target,
        SHA=SHA,
        PYTHON_DIGEST=PY_DIGEST,
        FRONTEND_DIGEST=FE_DIGEST,
        RUNNER_TEMP=str(harness.root),
        GITHUB_STEP_SUMMARY=str(summary),
        GITHUB_OUTPUT=str(output),
        STUB_SSM_OUTPUT=plan.rstrip("\n"),
    )
    planned = _staging_run("plan", "Plan on the staging node", env, work)

    assert planned.returncode == 0, planned.stderr
    assert harness.calls() == [["ssm_run", "plan", SHA]]
    assert Path(f"{harness.log}.digests").read_text() == f"{PY_DIGEST} {FE_DIGEST}\n"
    assert _unbracketed(planned.stdout) == plan
    assert summary.read_text() == f"### Staging plan for `{SHA}`\n\n````\n{plan}````\n"
    for name in ("Plan on the staging node",):
        step = _staging_step("plan", name)
        assert _children("      - " + step, "        env:") == [
            "SHA: ${{ steps.gate.outputs.sha }}",
            "PYTHON_DIGEST: ${{ steps.gate.outputs.python_digest }}",
            "FRONTEND_DIGEST: ${{ steps.gate.outputs.frontend_digest }}",
        ]

    parsed = _staging_run("plan", "Read the plan", env, work)
    assert parsed.returncode == 0, parsed.stderr
    assert output.read_bytes() == _outputs(SHA, "0486,0487", "")
    # The parse step is told which commit to expect by the gate's output.
    step = _staging_step("plan", "Read the plan")
    assert _children("      - " + step, "        env:") == ["SHA: ${{ steps.gate.outputs.sha }}"]


def test_a_plan_that_stopped_or_lists_missing_secrets_is_not_read(harness: Harness) -> None:
    work = _workspace(harness)
    plan = _plan_output([], production=False)
    for extra in ("Stopped at lock: x\n", "Missing secret files: a, b\n"):
        (harness.root / "plan.txt").write_text(plan + extra)
        output = harness.root / "github-output"
        output.unlink(missing_ok=True)
        env = harness.env(DEPLOY_TARGET="staging", SHA=SHA, RUNNER_TEMP=str(harness.root))
        env["GITHUB_OUTPUT"] = str(output)
        assert _staging_run("plan", "Read the plan", env, work).returncode != 0, extra
        assert not output.exists() or output.read_text() == ""


# --- instance output -----------------------------------------------------------------------------

INSTANCE_STEPS = {
    "plan": ("Plan on the staging node", "plan.txt"),
    "apply": ("Apply on the staging node", "apply.txt"),
}


@pytest.mark.parametrize("job", ["plan", "apply"])
def test_instance_output_is_bracketed_in_stop_commands(job: str) -> None:
    name, file = INSTANCE_STEPS[job]
    (block,) = _run_blocks(_staging_step(job, name))
    lines = block.splitlines()

    stop = lines.index('echo "::stop-commands::$token"')
    assert lines[stop - 1] == 'token="$(uuidgen)"'
    trap = lines.index("trap 'echo \"::$token::\"' EXIT")
    send = next(i for i, line in enumerate(lines) if "deploy/production/ssm_run.sh" in line)
    prints = [
        i
        for i, line in enumerate(lines)
        if line.startswith(("sed ", "cat ", "echo ")) and f"$RUNNER_TEMP/{file}" in line
    ]
    summary = next(i for i, line in enumerate(lines) if "GITHUB_STEP_SUMMARY" in line)
    assert stop < trap < send
    assert [lines[i] for i in prints] == [f"sed -e '$a\\' \"$RUNNER_TEMP/{file}\" || true"]
    assert send < prints[0] < summary
    # No other step sends to the instance or prints what it said.
    for other_job in ("plan", "apply"):
        for step in _staging_steps(other_job):
            if step.startswith(f"name: {INSTANCE_STEPS[other_job][0]}\n"):
                continue
            assert "ssm_run.sh" not in step, step
            for output in ("plan.txt", "apply.txt"):
                assert output not in step or "parse_plan.sh" in step, step


@pytest.mark.parametrize("job", ["plan", "apply"])
@pytest.mark.parametrize("status", [0, 3])
def test_instance_output_cannot_issue_workflow_commands(
    harness: Harness, job: str, status: int
) -> None:
    work = _workspace(harness)
    hostile = "::add-mask::x\n::error::injected\n::stop-commands::guess\nlast line, no newline"
    env = harness.env(
        SHA=SHA,
        MIGRATIONS="",
        RUNNER_TEMP=str(harness.root),
        GITHUB_STEP_SUMMARY=str(harness.root / "summary.md"),
        STUB_SSM_OUTPUT=hostile,
        STUB_SSM_STATUS=str(status),
    )
    result = _staging_run(job, INSTANCE_STEPS[job][0], env, work)

    assert result.returncode == status
    assert _unbracketed(result.stdout) == hostile + "\n"


# --- the apply job -------------------------------------------------------------------------------


def test_apply_checks_the_tip_before_aws_and_a_superseded_run_never_marks_applied(
    harness: Harness,
) -> None:
    steps = _staging_steps("apply")
    names = [step.split("\n", 1)[0] for step in steps]
    assert names[0] == "name: Require the deploy variables"
    assert names[1].startswith("uses: actions/checkout@")
    assert [names[i] for i in (2, 4, 5)] == [
        "name: Check the commit is still current",
        "name: Apply on the staging node",
        "name: Mark applied",
    ]
    assert len(names) == 6
    assert steps[3].startswith("uses: aws-actions/configure-aws-credentials@")
    check = steps[2]
    assert "id: current\n" in check
    assert "if:" not in check
    assert _children("      - " + check, "        env:") == [
        "SHA: ${{ needs.plan.outputs.sha }}",
        "EXPLICIT: ${{ needs.plan.outputs.explicit }}",
        "GH_TOKEN: ${{ github.token }}",
        "REPOSITORY: ${{ github.repository }}",
    ]
    # The AWS session, the send and the mark all wait for the check's verdict.
    for step in steps[3:]:
        assert re.search(rf"^        {re.escape(CURRENT)}$", step, flags=re.MULTILINE), step
    for step in steps[:3]:
        assert CURRENT not in step

    work = harness.root / "work"
    scripts = work / "deploy" / "staging"
    scripts.mkdir(parents=True)
    harness._write_stub(scripts / "rollout_gate.sh", GATE.read_text())
    summary = harness.root / "summary.md"
    output = harness.root / "github-output"

    def check_step(explicit: str, tip: str, **extra: str) -> tuple[int, str, str]:
        env = harness.env(
            SHA=SHA,
            EXPLICIT=explicit,
            GH_TOKEN="test-token",
            REPOSITORY="example/repo",
            RUNNER_TEMP=str(harness.root),
            GITHUB_OUTPUT=str(output),
            GITHUB_STEP_SUMMARY=str(summary),
            STUB_TIP=tip,
            **extra,
        )
        output.unlink(missing_ok=True)
        summary.unlink(missing_ok=True)
        harness.log.unlink(missing_ok=True)
        result = _staging_run("apply", "Check the commit is still current", env, work)
        return (
            result.returncode,
            output.read_text() if output.exists() else "",
            summary.read_text() if summary.exists() else "",
        )

    # Main moved: the step ends green with current=false and a note, and it read the tip once.
    code, written, noted = check_step("false", TIP)
    assert code == 0
    assert written == "current=false\n"
    assert "superseded" in noted
    assert TIP[:7] not in noted
    assert harness.calls() == [["gh", "api", "repos/example/repo/commits/main", "--jq", ".sha"]]
    # Main did not move.
    code, written, noted = check_step("false", SHA)
    assert code == 0
    assert written == "current=true\n"
    assert "superseded" not in noted
    # An explicit sha skips the tip check without a call.
    code, written, noted = check_step("true", TIP)
    assert (code, written, noted) == (0, "current=true\n", "")
    assert harness.calls() == []
    # An unreadable tip is a failure, not a pass.
    code, written, noted = check_step("false", TIP, STUB_GH_FAIL="1")
    assert code != 0
    assert "current=true" not in written


def test_apply_sends_the_planned_values_without_a_host_digest(harness: Harness) -> None:
    work = _workspace(harness)
    summary = harness.root / "summary.md"
    step = _staging_step("apply", "Apply on the staging node")
    assert _children("      - " + step, "        env:") == [
        "SHA: ${{ needs.plan.outputs.sha }}",
        "MIGRATIONS: ${{ needs.plan.outputs.migrations }}",
        "PYTHON_DIGEST: ${{ needs.plan.outputs.python_digest }}",
        "FRONTEND_DIGEST: ${{ needs.plan.outputs.frontend_digest }}",
    ]
    assert "HOST_DIGEST" not in _text()
    for migrations in ("", "0486,0487"):
        harness.log.unlink(missing_ok=True)
        Path(f"{harness.log}.digests").unlink(missing_ok=True)
        summary.unlink(missing_ok=True)
        env = harness.env(
            SHA=SHA,
            MIGRATIONS=migrations,
            PYTHON_DIGEST=PY_DIGEST,
            FRONTEND_DIGEST=FE_DIGEST,
            RUNNER_TEMP=str(harness.root),
            GITHUB_STEP_SUMMARY=str(summary),
            STUB_SSM_OUTPUT="applied\n",
            STUB_SSM_STATUS="3",
        )
        result = _staging_run("apply", "Apply on the staging node", env, work)

        assert result.returncode == 3
        assert harness.calls() == [["ssm_run", "apply", SHA, migrations]]
        assert Path(f"{harness.log}.digests").read_text() == f"{PY_DIGEST} {FE_DIGEST}\n"
        assert _unbracketed(result.stdout) == "applied\n"
        assert summary.read_text() == f"### Staging apply of `{SHA}`\n\n````\napplied\n````\n"


def test_digests_reach_ssm_run_through_the_environment_only() -> None:
    for job, name in (("plan", "Plan on the staging node"), ("apply", "Apply on the staging node")):
        (block,) = _run_blocks(_staging_step(job, name))
        (call,) = [line for line in block.splitlines() if "deploy/production/ssm_run.sh" in line]
        assert "DIGEST" not in call
        assert "${{" not in block
    for line in _code(_text()):
        if "python_digest" in line or "frontend_digest" in line:
            assert re.fullmatch(
                r"(?:python_digest|frontend_digest|PYTHON_DIGEST|FRONTEND_DIGEST): "
                r"\$\{\{ (?:steps\.gate|needs\.plan)\.outputs\.(?:python|frontend)_digest \}\}",
                line.strip(),
            ), line


# --- the record job ------------------------------------------------------------------------------


def test_record_runs_only_after_applied_true_and_applied_is_set_only_after_the_apply_step() -> None:
    code = _code(_text())
    assert not any("continue-on-error" in line for line in code)
    # No status function could run a step after a failure (or a cancel) and mark or record.
    for line in code:
        assert not re.search(r"\b(?:always|failure|cancelled)\(\)", line), line
    apply_steps = _staging_steps("apply")
    mark = apply_steps[-1]
    assert mark.startswith("name: Mark applied\n")
    assert "id: done\n" in mark
    assert _run_blocks(mark) == ['set -euo pipefail\necho applied=true >> "$GITHUB_OUTPUT"\n']
    names = [step.split("\n", 1)[0] for step in apply_steps]
    assert names.index("name: Apply on the staging node") == len(apply_steps) - 2
    assert _text().count("steps.done.outputs.applied") == 1
    assert _text().count("applied=true") == 1
    assert _text().count("needs.apply.outputs.applied") == 1

    record = _staging_steps("record")
    assert [step.split("\n", 1)[0] for step in record][1:] == ["name: Record the staged commit"]
    assert record[0].startswith("uses: actions/checkout@")
    step = record[1]
    assert _children("      - " + step, "        env:") == [
        "SHA: ${{ needs.plan.outputs.sha }}",
        "GH_TOKEN: ${{ github.token }}",
        "REPOSITORY: ${{ github.repository }}",
    ]
    assert _run_blocks(step) == ['set -euo pipefail\ndeploy/staging/record_staged.sh "$SHA"\n']
    assert "if:" not in step


def test_the_record_step_passes_the_sha_and_the_token_environment_to_the_script(
    harness: Harness,
) -> None:
    work = harness.root / "work"
    scripts = work / "deploy" / "staging"
    scripts.mkdir(parents=True)
    harness._write_stub(
        scripts / "record_staged.sh",
        '#!/bin/bash\nprintf "record\\t%s\\n" "$*" >> "$STUB_LOG"\n'
        'printf "%s %s\\n" "$GH_TOKEN" "$REPOSITORY" >> "$STUB_LOG.env"\n',
    )
    env = harness.env(SHA=SHA, GH_TOKEN="test-token", REPOSITORY="example/repo")
    result = _staging_run("record", "Record the staged commit", env, work)

    assert result.returncode == 0, result.stderr
    assert harness.calls() == [["record", SHA]]
    assert Path(f"{harness.log}.env").read_text() == "test-token example/repo\n"


# --- checkouts, target and the paused job --------------------------------------------------------


@pytest.mark.parametrize("job", ["plan", "apply", "record"])
def test_scripts_come_from_a_main_checkout_with_deploy_target_staging(job: str) -> None:
    text = _text()
    assert re.findall(r"^[ \t]*ref:.*$", text, flags=re.MULTILINE) == ["          ref: main"] * 3
    assert "working-directory:" not in text

    steps = _staging_steps(job)
    (checkout,) = [i for i, step in enumerate(steps) if "uses: actions/checkout@" in step]
    assert _children("      - " + steps[checkout], "        with:") == [
        "ref: main",
        "persist-credentials: false",
    ]
    scripts = [i for i, step in enumerate(steps) if "deploy/" in step]
    assert scripts
    assert checkout < min(scripts)
    configures = [i for i, step in enumerate(steps) if "configure-aws-credentials@" in step]
    sends = [i for i, step in enumerate(steps) if "ssm_run.sh" in step]
    for configure, send in zip(configures, sends, strict=True):
        assert checkout < configure < send
    invoked = re.findall(r"deploy/(?:production|staging)/\w+\.sh[^\n>|]*", "".join(steps))
    assert (
        invoked
        == {
            "plan": [
                "deploy/staging/rollout_gate.sh start ",
                'deploy/production/ssm_run.sh plan "$SHA" ',
                'deploy/production/parse_plan.sh "$RUNNER_TEMP/plan.txt" "$SHA" ',
            ],
            "apply": [
                'deploy/staging/rollout_gate.sh current "$SHA" "$EXPLICIT" ',
                'deploy/production/ssm_run.sh apply "$SHA" "$MIGRATIONS" ',
            ],
            "record": ['deploy/staging/record_staged.sh "$SHA"'],
        }[job]
    )
    # The target is set once per job that talks to the instance, and only as staging.
    lines = [line.strip() for line in _code(text) if "DEPLOY_TARGET" in line]
    assert lines == ["DEPLOY_TARGET: staging"] * 2


def test_the_paused_job_has_no_permissions_and_sends_nothing(tmp_path: Path) -> None:
    body = _staging_jobs()["paused"]
    assert "    permissions: {}\n" in body
    assert "uses:" not in body
    assert "secrets." not in body
    assert "needs:" not in body
    assert "environment" not in body
    steps = _staging_steps("paused")
    assert len(steps) == 1
    (block,) = _run_blocks(steps[0])
    assert PAUSED_MESSAGE in block
    assert "ssm" not in block
    assert "aws" not in block

    summary = tmp_path / "summary.md"
    result = _run_step(
        "paused",
        steps[0].split("\n", 1)[0].removeprefix("name: "),
        {"PATH": "/usr/bin:/bin", "GITHUB_STEP_SUMMARY": str(summary)},
        tmp_path,
        STAGING_WORKFLOW,
    )
    assert result.returncode == 0, result.stderr
    assert summary.read_text() == PAUSED_MESSAGE + "\n"


def test_the_workflow_holds_no_account_instance_region_or_address() -> None:
    text = _text()

    assert not re.search(r"\b\d{12}\b", text)
    assert not re.search(r"\b(?:mi|i)-[0-9a-f]{8,17}\b", text)
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)
    assert not re.search(r"\b(?:us|eu|ap|sa|ca|me|af|il|mx)-[a-z]+-\d\b", text)
    assert not re.search(r"arn:aws:iam::\d", text)
