"""Production deploy assets: the entry script, the Systems Manager document and its runner, the
OIDC role policies, the ``deploy-production`` workflow and the pins.

The shell scripts are run for real with ``bash`` against stub ``git``, ``aws``, ``gh`` and python
executables that append their argv (tab-separated, one invocation per line) to a file, so these
tests assert the exact commands the scripts would run without touching a repository, a database,
AWS or GitHub. The workflow is checked as text, and its shell blocks are extracted and run.
"""

from __future__ import annotations

import dataclasses
import fcntl
import json
import os
import re
import stat
import subprocess
import time
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from scripts import staging_rollout as sr
from scripts.rollout_targets import STAGING

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DIR = ROOT / "deploy" / "production"
ENTRY = PRODUCTION_DIR / "deploy_entry.sh"
PLAN_DOCUMENT = PRODUCTION_DIR / "ssm-document-plan.json"
APPLY_DOCUMENT = PRODUCTION_DIR / "ssm-document-apply.json"
REQUIREMENTS = PRODUCTION_DIR / "requirements.txt"
SSM_RUN = PRODUCTION_DIR / "ssm_run.sh"
PARSE_PLAN = PRODUCTION_DIR / "parse_plan.sh"
IAM_DIR = PRODUCTION_DIR / "iam"
PLAN_TRUST = IAM_DIR / "plan-trust-policy.json"
APPLY_TRUST = IAM_DIR / "apply-trust-policy.json"
PLAN_PERMISSIONS = IAM_DIR / "plan-permissions-policy.json"
APPLY_PERMISSIONS = IAM_DIR / "apply-permissions-policy.json"
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-production.yml"

SHA = "0123456789abcdef0123456789abcdef01234567"
MIGRATIONS = "0486,0487"
DIGEST = "0123456789ab"

# The entry script's arguments, in order; the two Systems Manager documents each fix the action.
PARAMETERS = ("action", "sha", "migrations", "hostDigest")

# One table of values that must be refused, keyed by argument (and Systems Manager parameter)
# name. The entry script, ssm_run.sh and the document pattern tests all read it, so the layers
# cannot drift apart.
BAD_VALUES: dict[str, list[str]] = {
    "action": ["destroy", "plan ; id", ""],
    "sha": [
        SHA.upper(),
        SHA[:39],
        SHA + "0",
        "$(id)",
        SHA + "\n",
        " " + SHA,
        # Unicode lookalikes: an ARABIC-INDIC DIGIT, a FULLWIDTH DIGIT and a FULLWIDTH LETTER.
        # A locale-aware regex range in a UTF-8 locale accepts these; the script must not.
        SHA[:-1] + "\u0660",
        SHA[:-1] + "\uff10",
        SHA[:-1] + "\uff41",
    ],
    "migrations": ["0486;id", "0486,", "abc", "04861", "048\u0666"],
    "hostDigest": [DIGEST[:-1], DIGEST + "0", DIGEST.upper(), "$(id)", DIGEST[:-1] + "\u0661"],
}
# Refusals beyond the per-value patterns: the digest is required for apply and empty for plan.
ENTRY_ONLY_BAD_ARGUMENTS = [
    pytest.param(["apply", SHA, "", ""], id="apply-without-digest"),
    pytest.param(["plan", SHA, "", DIGEST], id="plan-with-digest"),
    pytest.param(["plan", SHA, "", "", "extra"], id="too-many-arguments"),
    pytest.param(["plan"], id="no-sha"),
]
DEFAULTS = {"action": "apply", "sha": SHA, "migrations": MIGRATIONS, "hostDigest": DIGEST}


def _bad_argument_vectors() -> list[object]:
    cases: list[object] = []
    for name, values in BAD_VALUES.items():
        for value in values:
            args = {**DEFAULTS, name: value}
            cases.append(
                pytest.param(
                    [args[key] for key in PARAMETERS], id=f"{name}-{value!r}".replace(" ", "_")
                )
            )
    return cases + list(ENTRY_ONLY_BAD_ARGUMENTS)


# Each stub appends one tab-separated line per invocation to $STUB_LOG.
GIT_STUB = r"""#!/bin/bash
IFS=$'\t'
printf 'git\t%s\n' "$*" >> "$STUB_LOG"
IFS=' '
case " $* " in
    *" merge-base "*) exit "${STUB_GIT_MERGE_BASE_STATUS:-0}" ;;
esac
exit "${STUB_GIT_STATUS:-0}"
"""
# The python stub also reads stdin to EOF and records how many bytes it received.
PYTHON_STUB = r"""#!/bin/bash
IFS=$'\t'
stdin_bytes=$(wc -c)
printf 'python\t%s\n' "$*" >> "$STUB_LOG"
printf 'python-cwd\t%s\n' "$PWD" >> "$STUB_LOG"
printf 'python-stdin-bytes\t%s\n' "${stdin_bytes//[[:space:]]/}" >> "$STUB_LOG"
echo "rollout output"
exit "${STUB_PYTHON_STATUS:-0}"
"""
# The aws stub answers send-command with $STUB_COMMAND_ID and each get-command-invocation with the
# next word of $STUB_STATUSES (the last word repeats): a Systems Manager status, or Missing (the
# InvocationDoesNotExist race right after a send) or Denied (any other AWS error).
AWS_STUB = r"""#!/bin/bash
IFS=$'\t'
printf 'aws\t%s\n' "$*" >> "$STUB_LOG"
IFS=' '
case "$2" in
    send-command)
        if [[ -n "${STUB_SEND_ERROR:-}" ]]; then
            echo "$STUB_SEND_ERROR" >&2
            exit 254
        fi
        printf '{"Command": {"CommandId": "%s", "Status": "Pending"}}\n' \
            "${STUB_COMMAND_ID-0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0}"
        ;;
    get-command-invocation)
        count=$(( $(cat "$STUB_POLLS" 2>/dev/null || echo 0) + 1 ))
        echo "$count" > "$STUB_POLLS"
        read -r -a statuses <<< "${STUB_STATUSES:-Success}"
        last=$(( ${#statuses[@]} - 1 ))
        status="${statuses[$(( count - 1 < last ? count - 1 : last ))]}"
        case "$status" in
            Missing)
                echo "An error occurred (InvocationDoesNotExist) when calling the" \
                    "GetCommandInvocation operation: " >&2
                exit 254
                ;;
            Denied)
                echo "An error occurred (AccessDeniedException)" >&2
                exit 254
                ;;
        esac
        out=$'remote output\n'
        [[ -v STUB_STDOUT ]] && out="$STUB_STDOUT"
        jq -n --arg status "$status" --arg out "$out" '{
            Status: $status,
            StatusDetails: $status,
            ResponseCode: (if $status == "Success" then 0 else 2 end),
            StandardOutputContent: $out,
            StandardErrorContent: "remote stderr\n"
        }'
        ;;
    *) exit 2 ;;
esac
"""
# The gh stub serves `gh api <path>` from JSON files in $STUB_GH_DIR.
GH_STUB = r"""#!/bin/bash
IFS=$'\t'
printf 'gh\t%s\n' "$*" >> "$STUB_LOG"
IFS=' '
[[ "$1" == api && $# -eq 2 ]] || exit 2
case "$2" in
    *"/statuses?per_page=100")
        id="${2%/statuses*}"
        cat "$STUB_GH_DIR/statuses-${id##*/}.json"
        ;;
    *"/deployments?environment=staging&per_page=100") cat "$STUB_GH_DIR/deployments.json" ;;
    *) exit 2 ;;
esac
"""


class Harness:
    """Stub executables and an environment the deploy scripts can run in."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.checkout = tmp_path / "checkout"
        self.checkout.mkdir()
        self.log = tmp_path / "calls.log"
        self.lock = tmp_path / "deploy.lock"
        self.log_dir = tmp_path / "logs"
        self.python = tmp_path / "python-stub"
        self.polls = tmp_path / "polls"
        self.gh_dir = tmp_path / "gh"
        self._write_stub(self.bin / "git", GIT_STUB)
        self._write_stub(self.bin / "aws", AWS_STUB)
        self._write_stub(self.bin / "gh", GH_STUB)
        self._write_stub(self.python, PYTHON_STUB)

    @staticmethod
    def _write_stub(path: Path, body: str) -> None:
        path.write_text(body)
        path.chmod(0o755)

    def env(self, **extra: str) -> dict[str, str]:
        return {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "CHECKOUT": str(self.checkout),
            "PYTHON": str(self.python),
            "LOCK": str(self.lock),
            "LOG_DIR": str(self.log_dir),
            "STUB_LOG": str(self.log),
            **extra,
        }

    def run(
        self, args: list[str], *, stdin: bytes = b"", **extra_env: str
    ) -> subprocess.CompletedProcess[bytes]:
        return self.run_script(ENTRY, args, env=self.env(**extra_env), stdin=stdin)

    @staticmethod
    def run_script(
        script: Path, args: list[str], *, env: dict[str, str], stdin: bytes = b""
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(script), *args],
            env=env,
            input=stdin,
            capture_output=True,
            timeout=30,
            check=False,
        )

    def ssm(
        self, args: list[str], *, unset: Iterable[str] = (), **extra_env: str
    ) -> subprocess.CompletedProcess[bytes]:
        env = self.env(**{**SSM_ENV, "STUB_POLLS": str(self.polls), **extra_env})
        for name in unset:
            env.pop(name)
        return self.run_script(SSM_RUN, args, env=env)

    def aws_calls(self) -> list[list[str]]:
        return [call[1:] for call in self.calls() if call[0] == "aws"]

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [line.split("\t") for line in self.log.read_text().splitlines()]


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


# Validation must not depend on the caller's locale: a UTF-8 locale widens regex ranges.
LOCALES = ("C", "en_US.UTF-8")


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("args", _bad_argument_vectors())
def test_entry_rejects_bad_arguments(harness: Harness, args: list[str], locale: str) -> None:
    result = harness.run(args, LC_ALL=locale, LANG=locale)

    assert result.returncode != 0
    assert harness.calls() == []
    assert not harness.log_dir.exists()


def test_entry_plan_checks_out_the_sha_and_runs_the_production_plan(harness: Harness) -> None:
    result = harness.run(["plan", SHA, "", ""])

    assert result.returncode == 0, result.stderr
    checkout = str(harness.checkout)
    assert harness.calls() == [
        ["git", "-C", checkout, "fetch", "origin", "main"],
        ["git", "-C", checkout, "merge-base", "--is-ancestor", SHA, "origin/main"],
        ["git", "-C", checkout, "checkout", "--detach", SHA],
        ["python", "-m", "scripts.staging_rollout", "--target", "production", "plan"],
        ["python-cwd", checkout],
        ["python-stdin-bytes", "0"],
    ]
    assert b"rollout output" in result.stdout


def test_entry_apply_passes_exactly_the_validated_arguments(harness: Harness) -> None:
    result = harness.run(["apply", SHA, MIGRATIONS, DIGEST])

    assert result.returncode == 0, result.stderr
    checkout = str(harness.checkout)
    assert harness.calls() == [
        ["git", "-C", checkout, "fetch", "origin", "main"],
        ["git", "-C", checkout, "merge-base", "--is-ancestor", SHA, "origin/main"],
        ["git", "-C", checkout, "checkout", "--detach", SHA],
        [
            "python",
            "-m",
            "scripts.staging_rollout",
            "--target",
            "production",
            "apply",
            "--sha",
            SHA,
            "--migrations",
            MIGRATIONS,
            "--host-digest",
            DIGEST,
        ],
        ["python-cwd", checkout],
        ["python-stdin-bytes", "0"],
    ]


def test_entry_apply_without_migrations_omits_the_flag(harness: Harness) -> None:
    result = harness.run(["apply", SHA, "", DIGEST])

    assert result.returncode == 0, result.stderr
    rollout = [call for call in harness.calls() if call[0] == "python"]
    assert rollout == [
        [
            "python",
            "-m",
            "scripts.staging_rollout",
            "--target",
            "production",
            "apply",
            "--sha",
            SHA,
            "--host-digest",
            DIGEST,
        ]
    ]


def test_entry_tees_output_to_a_named_log_and_returns_the_rollout_status(
    harness: Harness,
) -> None:
    result = harness.run(["apply", SHA, "", DIGEST], STUB_PYTHON_STATUS="3")

    assert result.returncode == 3
    logs = list(harness.log_dir.iterdir())
    assert len(logs) == 1
    assert re.fullmatch(r"\d{8}T\d{6}Z-apply-0123456\.log", logs[0].name)
    assert "rollout output" in logs[0].read_text()
    assert b"rollout output" in result.stdout


def test_entry_refuses_a_commit_that_is_not_on_main(harness: Harness) -> None:
    result = harness.run(["apply", SHA, MIGRATIONS, DIGEST], STUB_GIT_MERGE_BASE_STATUS="1")

    assert result.returncode != 0
    assert b"commit is not on main" in result.stderr
    (log,) = harness.log_dir.iterdir()
    assert "commit is not on main" in log.read_text()
    checkout = str(harness.checkout)
    assert harness.calls() == [
        ["git", "-C", checkout, "fetch", "origin", "main"],
        ["git", "-C", checkout, "merge-base", "--is-ancestor", SHA, "origin/main"],
    ]


def test_entry_does_not_run_the_rollout_when_git_fails(harness: Harness) -> None:
    result = harness.run(["plan", SHA, "", ""], STUB_GIT_STATUS="1")

    assert result.returncode != 0
    assert all(call[0] == "git" for call in harness.calls())


def test_second_deploy_exits_75_while_the_lock_is_held(harness: Harness) -> None:
    with harness.lock.open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = harness.run(["plan", SHA, "", ""])

    assert result.returncode == 75
    assert b"another deploy is running" in result.stderr
    assert harness.calls() == []


def test_entry_ignores_stdin(harness: Harness) -> None:
    result = harness.run(["plan", SHA, "", ""], stdin=b"would-be-read\n" * 100)

    assert result.returncode == 0, result.stderr
    assert ["python-stdin-bytes", "0"] in harness.calls()


def test_entry_script_is_bash_strict_and_has_no_sudo() -> None:
    text = ENTRY.read_text()
    lines = text.splitlines()
    code = "\n".join(line for line in lines if not line.lstrip().startswith("#"))

    assert lines[0] == "#!/bin/bash"
    assert "set -euo pipefail" in lines
    assert not re.search(r"\bsudo\b", code)
    assert not re.search(r"\beval\b", code)
    assert "< /dev/null" in code
    assert ENTRY.stat().st_mode & stat.S_IXUSR
    assert os.access(ENTRY, os.X_OK)
    for name in ("CHECKOUT", "PYTHON", "LOCK", "LOG_DIR"):
        assert re.search(rf'^: "\$\{{{name}:=[^}}]+\}}"$', text, re.MULTILINE)


# Each action has its own document, so the plan role can never send an apply.
DOCUMENTS = {"plan": PLAN_DOCUMENT, "apply": APPLY_DOCUMENT}
DOCUMENT_NAMES = {"plan": "csa-production-plan", "apply": "csa-production-apply"}
DOCUMENT_PATTERNS = {
    "plan": {"sha": "^[0-9a-f]{40}$"},
    "apply": {
        "sha": "^[0-9a-f]{40}$",
        "migrations": "^([0-9]{4}(,[0-9]{4})*)?$",
        "hostDigest": "^[0-9a-f]{12}$",
    },
}
ENTRY_COMMAND = "exec runuser -u ubuntu -- /usr/local/sbin/csa-prod-deploy "
DOCUMENT_COMMANDS = {
    "plan": ENTRY_COMMAND + 'plan "{{ sha }}" "" ""',
    "apply": ENTRY_COMMAND + 'apply "{{ sha }}" "{{ migrations }}" "{{ hostDigest }}"',
}


def _document(kind: str) -> dict[str, Any]:
    return json.loads(DOCUMENTS[kind].read_text())


@pytest.mark.parametrize("kind", ["plan", "apply"])
def test_document_parameters_have_the_exact_patterns_and_the_one_command(kind: str) -> None:
    document = _document(kind)

    assert set(document) == {"schemaVersion", "description", "parameters", "mainSteps"}
    assert document["schemaVersion"] == "2.2"
    assert f"Published in AWS as {DOCUMENT_NAMES[kind]}." in document["description"]
    parameters = document["parameters"]
    assert list(parameters) == list(DOCUMENT_PATTERNS[kind])
    for name, pattern in DOCUMENT_PATTERNS[kind].items():
        assert parameters[name]["type"] == "String"
        assert parameters[name]["description"]
        assert parameters[name]["allowedPattern"] == pattern
    defaults = {name: spec["default"] for name, spec in parameters.items() if "default" in spec}
    assert defaults == ({"migrations": ""} if kind == "apply" else {})

    (step,) = document["mainSteps"]
    assert step["action"] == "aws:runShellScript"
    assert step["name"] == "deploy"
    assert step["inputs"]["timeoutSeconds"] == "2400"
    commands = step["inputs"]["runCommand"]
    assert commands == ["set -eu", DOCUMENT_COMMANDS[kind]]

    command = commands[1]
    placeholders = re.findall(r"\{\{[^}]*\}\}", command)
    names = [re.fullmatch(r"\{\{ (\w+) \}\}", item) for item in placeholders]
    assert all(names)
    assert [match.group(1) for match in names if match] == list(DOCUMENT_PATTERNS[kind])
    assert len(re.findall(r'"\{\{ \w+ \}\}"', command)) == len(placeholders)
    # Nothing else in the document may smuggle in a placeholder or another command.
    assert "{{" not in json.dumps({k: v for k, v in document.items() if k != "mainSteps"})


def _ssm_run_default(name: str) -> int:
    (value,) = re.findall(rf': "\$\{{{name}:=(\d+)\}}"', SSM_RUN.read_text())
    return int(value)


@pytest.mark.parametrize("kind", ["plan", "apply"])
def test_document_timeout_outlasts_the_runner_and_fits_inside_the_job(kind: str) -> None:
    timeout = int(_document(kind)["mainSteps"][0]["inputs"]["timeoutSeconds"])
    # The runner's wait is bounded by elapsed time, not by a number of polls.
    runner_wait = _ssm_run_default("SSM_MAX_SECONDS")
    job_minutes = [int(m) for m in re.findall(r"^\s+timeout-minutes: (\d+)$", _workflow(), re.M)]

    assert timeout == 2400
    assert runner_wait == 2100
    assert "SSM_MAX_POLLS" not in SSM_RUN.read_text()
    # A slow apply must not be killed while the runner still waits for it, and the job must
    # outlive the command.
    assert timeout > runner_wait
    assert job_minutes == [45, 45]
    assert all(runner_wait < timeout < minutes * 60 for minutes in job_minutes)


def test_each_document_fixes_its_action_literally() -> None:
    plan = _document("plan")
    assert not {"action", "migrations", "hostDigest"} & set(plan["parameters"])
    plan_command = plan["mainSteps"][0]["inputs"]["runCommand"][1]
    assert plan_command.startswith(ENTRY_COMMAND + "plan ")
    assert "apply" not in json.dumps(plan)
    assert "{{ action" not in plan_command

    apply = _document("apply")
    assert "action" not in apply["parameters"]
    apply_command = apply["mainSteps"][0]["inputs"]["runCommand"][1]
    assert apply_command.startswith(ENTRY_COMMAND + "apply ")
    assert "{{ action" not in apply_command


def test_the_single_deploy_document_and_shared_role_files_are_gone() -> None:
    assert not (PRODUCTION_DIR / "ssm-document.json").exists()
    assert not (IAM_DIR / "trust-policy.json").exists()
    assert not (IAM_DIR / "permissions-policy.json").exists()
    assert sorted(path.name for path in IAM_DIR.iterdir()) == sorted(
        path.name for path in (PLAN_TRUST, APPLY_TRUST, PLAN_PERMISSIONS, APPLY_PERMISSIONS)
    )


@pytest.mark.parametrize("kind", ["plan", "apply"])
def test_document_patterns_reject_injection(kind: str) -> None:
    parameters = _document(kind)["parameters"]

    for name in parameters:
        pattern = parameters[name]["allowedPattern"]
        for value in BAD_VALUES[name]:
            assert re.fullmatch(pattern, value) is None, (name, value)
        assert re.fullmatch(pattern, DEFAULTS[name]), name
    if kind == "apply":
        assert re.fullmatch(parameters["migrations"]["allowedPattern"], "")
        assert re.fullmatch(parameters["hostDigest"]["allowedPattern"], "") is None


def test_requirements_pin_matches_pyproject() -> None:
    lines = REQUIREMENTS.read_text().splitlines()
    assert len(lines) == 1
    match = re.fullmatch(r"psycopg\[binary\]==(\S+)", lines[0])
    assert match

    dependencies = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    pinned = [
        re.fullmatch(r"psycopg\[[^\]]*\]==(\S+)", dependency)
        for dependency in dependencies
        if dependency.startswith("psycopg[")
    ]
    assert len(pinned) == 1 and pinned[0]
    assert match.group(1) == pinned[0].group(1)


# --- ssm_run.sh: send the document, wait, report -----------------------------------------------

REGION = "test-region-1"
INSTANCE = "mi-0123456789abcdef0"
COMMAND_ID = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"
SECRET = "test-secret-access-key-never-printed"
TOKEN = "test-session-token-never-printed"
SSM_ENV = {
    "AWS_REGION": REGION,
    "INSTANCE_ID": INSTANCE,
    "SSM_POLL_SECONDS": "0",
    "AWS_ACCESS_KEY_ID": "test-access-key-id",
    "AWS_SECRET_ACCESS_KEY": SECRET,
    "AWS_SESSION_TOKEN": TOKEN,
}


def _send_argv(document: str, parameters: str) -> list[str]:
    return [
        "ssm",
        "send-command",
        "--region",
        REGION,
        "--document-name",
        document,
        "--instance-ids",
        INSTANCE,
        "--parameters",
        parameters,
        "--timeout-seconds",
        "600",
        "--output",
        "json",
    ]


POLL_ARGV = [
    "ssm",
    "get-command-invocation",
    "--region",
    REGION,
    "--command-id",
    COMMAND_ID,
    "--instance-id",
    INSTANCE,
    "--output",
    "json",
]


@pytest.mark.parametrize("name", ["AWS_REGION", "INSTANCE_ID"])
@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_ssm_run_fails_closed_without_instance_or_region(
    harness: Harness, name: str, value: str | None
) -> None:
    if value is None:
        result = harness.ssm(["plan", SHA], unset=[name])
    else:
        result = harness.ssm(["plan", SHA], **{name: value})

    assert result.returncode == 64
    assert name.encode() in result.stderr
    assert harness.calls() == []


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("INSTANCE_ID", "mi-0123; id"),
        ("INSTANCE_ID", INSTANCE + "\n"),
        ("AWS_REGION", "$(id)"),
        ("SSM_POLL_SECONDS", "five"),
        ("SSM_MAX_SECONDS", "0"),
        ("SSM_MAX_SECONDS", "-1"),
        ("SSM_MAX_SECONDS", "1.5"),
    ],
)
def test_ssm_run_refuses_malformed_settings(harness: Harness, name: str, value: str) -> None:
    result = harness.ssm(["plan", SHA], **{name: value})

    assert result.returncode == 64
    assert name.encode() in result.stderr
    assert harness.calls() == []


# The plan document carries no migrations, so ssm_run.sh refuses them too (the entry script ignores
# them for plan).
SSM_ONLY_BAD_ARGUMENTS = [pytest.param(["plan", SHA, MIGRATIONS, ""], id="plan-with-migrations")]


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("args", _bad_argument_vectors() + SSM_ONLY_BAD_ARGUMENTS)
def test_ssm_run_rejects_bad_arguments(harness: Harness, args: list[str], locale: str) -> None:
    result = harness.ssm(args, LC_ALL=locale, LANG=locale)

    assert result.returncode == 64
    assert harness.calls() == []


@pytest.mark.parametrize(
    ("status", "code"), [("Success", 0), ("Failed", 1), ("TimedOut", 1), ("Cancelled", 1)]
)
def test_ssm_run_sends_the_document_with_separate_parameters_and_exits_with_the_status(
    harness: Harness, status: str, code: int
) -> None:
    output = harness.root / "github-output"
    result = harness.ssm(
        ["apply", SHA, MIGRATIONS, DIGEST], STUB_STATUSES=status, GITHUB_OUTPUT=str(output)
    )

    assert result.returncode == code, result.stderr
    send, poll = harness.aws_calls()
    assert send == _send_argv("csa-production-apply", send[9])
    assert json.loads(send[9]) == {"sha": [SHA], "migrations": [MIGRATIONS], "hostDigest": [DIGEST]}
    assert poll == POLL_ARGV
    assert result.stdout == b"remote output\n"
    assert b"remote stderr\n" in result.stderr
    assert COMMAND_ID.encode() in result.stderr
    assert f"status {status}".encode() in result.stderr
    assert output.read_text() == f"command_id={COMMAND_ID}\n"
    for credential in (SECRET, TOKEN):
        assert credential.encode() not in result.stdout + result.stderr


def test_ssm_run_apply_without_migrations_omits_the_parameter(harness: Harness) -> None:
    # The apply document defaults migrations to "", so an empty list is left out rather than
    # sent as an explicit empty String; sha and hostDigest are always sent.
    assert _document("apply")["parameters"]["migrations"]["default"] == ""
    result = harness.ssm(["apply", SHA, "", DIGEST])

    assert result.returncode == 0, result.stderr
    send, _poll = harness.aws_calls()
    assert send == _send_argv("csa-production-apply", send[9])
    assert json.loads(send[9]) == {"sha": [SHA], "hostDigest": [DIGEST]}


@pytest.mark.parametrize("migrations", ["0486", MIGRATIONS])
def test_ssm_run_apply_sends_a_non_empty_migrations_list(
    harness: Harness, migrations: str
) -> None:
    result = harness.ssm(["apply", SHA, migrations, DIGEST])

    assert result.returncode == 0, result.stderr
    send, _poll = harness.aws_calls()
    assert json.loads(send[9]) == {
        "sha": [SHA],
        "migrations": [migrations],
        "hostDigest": [DIGEST],
    }


def test_ssm_run_plan_sends_only_the_sha_to_the_plan_document_and_prints_verbatim(
    harness: Harness,
) -> None:
    remote = "Target SHA: x\n  indented   line\n\nno final newline"
    result = harness.ssm(["plan", SHA], STUB_STDOUT=remote)

    assert result.returncode == 0, result.stderr
    send, _poll = harness.aws_calls()
    assert send == _send_argv("csa-production-plan", send[9])
    assert json.loads(send[9]) == {"sha": [SHA]}
    assert result.stdout == remote.encode()


def test_ssm_run_polls_through_inprogress_and_times_out(harness: Harness) -> None:
    result = harness.ssm(["plan", SHA], STUB_STATUSES="InProgress", SSM_MAX_SECONDS="1")

    assert result.returncode == 124
    calls = [call[1] for call in harness.aws_calls()]
    assert calls[0] == "send-command"
    assert len(calls) > 1
    assert set(calls[1:]) == {"get-command-invocation"}
    assert b"still InProgress after" in result.stderr
    assert b"giving up" in result.stderr


# A stub sleep that records the interval it was asked for and really waits $STUB_SLEEP_REAL
# seconds instead: the clock the runner reads (bash's SECONDS) moves only with real time.
SLEEP_STUB = r"""#!/bin/bash
printf 'sleep\t%s\n' "$*" >> "$STUB_LOG"
exec /bin/sleep "${STUB_SLEEP_REAL:-0}"
"""


def test_ssm_run_gives_up_by_elapsed_time_not_by_poll_count(harness: Harness) -> None:
    harness._write_stub(harness.bin / "sleep", SLEEP_STUB)
    polls: dict[str, int] = {}
    for speed, real in (("slow", "0.7"), ("fast", "0")):
        harness.log.unlink(missing_ok=True)
        harness.polls.unlink(missing_ok=True)
        started = time.monotonic()
        result = harness.ssm(
            ["plan", SHA],
            STUB_STATUSES="InProgress",
            SSM_POLL_SECONDS="5",
            SSM_MAX_SECONDS="2",
            STUB_SLEEP_REAL=real,
        )
        elapsed = time.monotonic() - started

        assert result.returncode == 124, result.stderr
        assert b"giving up" in result.stderr
        # The ceiling is wall-clock time since the send (whole seconds, so up to one early).
        assert elapsed >= 1
        sleeps = [call for call in harness.calls() if call[0] == "sleep"]
        assert sleeps and all(call == ["sleep", "5"] for call in sleeps)
        polls[speed] = sum(1 for call in harness.aws_calls() if call[1] == "get-command-invocation")
    # The same ceiling, a different number of polls: the bound is time, not a count. At 0.7 s a
    # poll, two seconds allow a handful; instant polls run many more before the ceiling.
    assert 1 <= polls["slow"] <= 6
    assert polls["fast"] > polls["slow"]


@pytest.mark.parametrize(
    ("statuses", "code", "polls"),
    [
        pytest.param("Pending InProgress Delayed Success", 0, 4, id="waits-until-done"),
        pytest.param("Missing Missing InProgress Success", 0, 4, id="tolerates-the-send-race"),
        pytest.param("Missing " * 5 + "Success", 0, 6, id="five-missing-polls-tolerated"),
        pytest.param("Missing " * 6 + "Success", 1, 6, id="the-sixth-missing-poll-fails"),
        pytest.param("Denied Success", 1, 1, id="other-errors-fail-at-once"),
    ],
)
def test_ssm_run_poll_outcomes(harness: Harness, statuses: str, code: int, polls: int) -> None:
    result = harness.ssm(["plan", SHA], STUB_STATUSES=statuses)

    assert result.returncode == code, result.stderr
    calls = [call[1] for call in harness.aws_calls()]
    assert calls == ["send-command"] + ["get-command-invocation"] * polls


@pytest.mark.parametrize("command_id", ["", "not-a-command-id", "$(id)", COMMAND_ID + "0"])
def test_ssm_run_refuses_a_malformed_command_id(harness: Harness, command_id: str) -> None:
    output = harness.root / "github-output"
    result = harness.ssm(["plan", SHA], STUB_COMMAND_ID=command_id, GITHUB_OUTPUT=str(output))

    assert result.returncode == 1
    assert [call[1] for call in harness.aws_calls()] == ["send-command"]
    assert not output.exists()


def test_ssm_run_stops_when_send_command_fails(harness: Harness) -> None:
    result = harness.ssm(["plan", SHA], STUB_SEND_ERROR="An error occurred (AccessDeniedException)")

    assert result.returncode == 1
    assert b"AccessDeniedException" in result.stderr
    assert [call[1] for call in harness.aws_calls()] == ["send-command"]


def _unquoted_expansions(script: str) -> list[str]:
    """Lines with a ``$`` expansion outside double quotes; the ``=~ $1`` regex operand excepted.

    A small quote tracker, not a shell parser: single quotes, ANSI-C ``$'...'`` strings, double
    quotes, ``\\`` escapes and nested ``$(...)`` are followed, a ``#`` after a blank ends the line.
    """
    offending: list[str] = []
    for raw in script.splitlines():
        if raw.lstrip().startswith("#"):
            continue
        line = raw.replace("=~ $1", "=~ ")
        stack = ["code"]
        i = 0
        while i < len(line):
            char, following = line[i], line[i + 1 : i + 2]
            if stack[-1] == "dq":
                if char == "\\":
                    i += 1
                elif char == '"':
                    stack.pop()
                elif char == "$" and following == "(":
                    stack.append("(")
                    i += 1
            elif char == "\\":
                i += 1
            elif char == "'" or (char == "$" and following == "'"):
                start = i + 1 if char == "'" else i + 2
                i = line.index("'", start)
            elif char == '"':
                stack.append("dq")
            elif char == "$" and following == "(":
                stack.append("(")
                i += 1
            elif char == "(":
                stack.append("(")
            elif char == ")" and stack[-1] == "(":
                stack.pop()
            elif char == "#" and (i == 0 or line[i - 1].isspace()):
                break
            elif char == "$" and re.match(r"[A-Za-z_{0-9@#?*!-]", following):
                offending.append(raw)
                break
            i += 1
    return offending


def test_the_quote_checker_finds_unquoted_expansions() -> None:
    assert _unquoted_expansions("echo \"$a\" '$b' $'\\n' \"$(cmd \"$c\" '$d')\"\n") == []
    assert _unquoted_expansions('[[ "$2" =~ $1 ]]\nx="$((n + 1))"\n# $comment\n') == []
    bad = ["echo $a", 'echo "$(cmd $b)"', "exit $?", "for x in ${list[@]}; do :; done"]
    assert _unquoted_expansions("\n".join(bad)) == bad


@pytest.mark.parametrize("script", [SSM_RUN, PARSE_PLAN], ids=lambda path: path.name)
def test_runner_scripts_are_bash_strict_quoted_and_executable(script: Path) -> None:
    text = script.read_text()
    lines = text.splitlines()
    code = "\n".join(line for line in lines if not line.lstrip().startswith("#"))

    assert lines[0] == "#!/bin/bash"
    strict = lines.index("set -euo pipefail")
    locale = lines.index("export LC_ALL=C")
    assert strict < locale < next(i for i, line in enumerate(lines) if "=~" in line)
    assert not re.search(r"\beval\b|\b(?:ba)?sh -c\b|\bset -x\b|\bsource\b", code)
    assert _unquoted_expansions(text) == []
    assert script.stat().st_mode & stat.S_IXUSR
    assert os.access(script, os.X_OK)


# --- parse_plan.sh: the three values apply must send ---------------------------------------------

PROD_LIKE = dataclasses.replace(
    STAGING, name="production", host_file=Path("/nonexistent/host.env"), staged_gate=True
)
HOST_VALUES = {"CSA_PROD_EXAMPLE": "1"}
PLAN_DIGEST = sr.host_settings_digest(HOST_VALUES)
OTHER_SHA = "f" * 40


def _plan_output(pending: list[str], *, sha: str = SHA, production: bool = True) -> str:
    """What ``format_plan`` prints for a production-like target (or staging, with no digest)."""
    target = PROD_LIKE if production else STAGING
    images = {app: f"ghcr.io/example/csa-{app}@sha256:{'1' * 64}" for app in sr.APPS}
    plan = sr.Plan(
        sha,
        images,
        {},
        pending,
        [],
        [],
        sr.plan_steps(pending, [], target=target),
        {},
        host_values=HOST_VALUES,
        staged_on="2026-11-09T01:02:03Z",
    )
    return sr.format_plan(plan, target=target) + "\n"


def _parse(
    harness: Harness, text: str, *args: str, locale: str = "C"
) -> subprocess.CompletedProcess[bytes]:
    plan = harness.root / "plan.txt"
    plan.write_bytes(text.encode())
    argv = list(args) if args else [str(plan), SHA]
    return harness.run_script(PARSE_PLAN, argv, env=harness.env(LC_ALL=locale, LANG=locale))


def _outputs(sha: str, migrations: str, digest: str) -> bytes:
    return f"sha={sha}\nmigrations={migrations}\nhost_digest={digest}\n".encode()


@pytest.mark.parametrize(
    ("text", "migrations"),
    [
        pytest.param(_plan_output(["0486", "0487"]), "0486,0487", id="two-migrations"),
        pytest.param(_plan_output(["0490"]), "0490", id="one-migration"),
        pytest.param(_plan_output([]), "", id="none"),
        pytest.param(_plan_output([]).rstrip("\n"), "", id="no-final-newline"),
        pytest.param(
            _plan_output(["0486"])
            + f"note: Target SHA: {OTHER_SHA}\n  Host settings digest: ffffffffffff\n"
            + "rollout said Pending migrations: 0999\n",
            "0486",
            id="embedded-lookalikes-ignored",
        ),
    ],
)
def test_parse_plan_reads_the_three_lines(harness: Harness, text: str, migrations: str) -> None:
    result = _parse(harness, text)

    assert result.returncode == 0, result.stderr
    assert result.stdout == _outputs(SHA, migrations, PLAN_DIGEST)


def _replace_line(text: str, prefix: str, new: str) -> str:
    return "".join(new if line.startswith(prefix) else line for line in text.splitlines(True))


GOOD_PLAN = _plan_output(["0486", "0487"])
HOSTILE_PLANS = [
    pytest.param(GOOD_PLAN + f"Target SHA: {SHA}\n", id="duplicate-target"),
    pytest.param(GOOD_PLAN + "Pending migrations: none\n", id="duplicate-pending"),
    pytest.param(GOOD_PLAN + f"Host settings digest: {PLAN_DIGEST}\n", id="duplicate-digest"),
    pytest.param(_plan_output(["0486"], production=False), id="missing-digest-staging-format"),
    pytest.param(_replace_line(GOOD_PLAN, "Target SHA: ", ""), id="missing-target"),
    pytest.param(_replace_line(GOOD_PLAN, "Pending migrations: ", ""), id="missing-pending"),
    pytest.param(GOOD_PLAN.replace("\n", "\r\n"), id="crlf-line-endings"),
    pytest.param(
        _replace_line(GOOD_PLAN, "Host settings digest: ", f"Host settings digest: {DIGEST}\r\n"),
        id="cr-in-digest",
    ),
    pytest.param(
        _replace_line(
            GOOD_PLAN, "Host settings digest: ", f"Host settings digest: {DIGEST.upper()}\n"
        ),
        id="uppercase-digest",
    ),
    pytest.param(
        _replace_line(GOOD_PLAN, "Host settings digest: ", "Host settings digest: \n"),
        id="empty-digest",
    ),
    pytest.param(
        _replace_line(GOOD_PLAN, "Pending migrations: ", "Pending migrations: 0486,0487\n"),
        id="migrations-without-the-printed-spacing",
    ),
    pytest.param(
        _replace_line(GOOD_PLAN, "Pending migrations: ", "Pending migrations: 0486, 048\u0666\n"),
        id="unicode-digit-migration",
    ),
    pytest.param(
        _replace_line(GOOD_PLAN, "Pending migrations: ", "Pending migrations: 0486; id\n"),
        id="injected-migrations",
    ),
    pytest.param(
        _replace_line(GOOD_PLAN, "Target SHA: ", f"Target SHA: {SHA[:-1]}\uff10\n"),
        id="fullwidth-digit-sha",
    ),
    pytest.param(_plan_output(["0486"], sha=OTHER_SHA), id="plan-for-another-commit"),
    pytest.param("", id="empty-output"),
]


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("text", HOSTILE_PLANS)
def test_parse_plan_refuses_hostile_output(harness: Harness, text: str, locale: str) -> None:
    result = _parse(harness, text, locale=locale)

    assert result.returncode != 0
    assert result.stdout == b""
    assert result.stderr.startswith(b"parse_plan: ")


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["{plan}", "$(id)"], id="bad-expected-sha"),
        pytest.param(["{plan}", SHA.upper()], id="uppercase-expected-sha"),
        pytest.param(["{missing}", SHA], id="missing-file"),
        pytest.param(["{plan}"], id="no-expected-sha"),
        pytest.param(["{plan}", SHA, "extra"], id="too-many-arguments"),
    ],
)
def test_parse_plan_refuses_bad_arguments(harness: Harness, args: list[str]) -> None:
    paths = {"{plan}": str(harness.root / "plan.txt"), "{missing}": str(harness.root / "none")}
    result = _parse(harness, GOOD_PLAN, *[paths.get(arg, arg) for arg in args])

    assert result.returncode != 0
    assert result.stdout == b""


# --- the OIDC role ------------------------------------------------------------------------------

AUD = "token.actions.githubusercontent.com:aud"
SUB = "token.actions.githubusercontent.com:sub"
INSTANCE_ARN = "arn:aws:ssm:<region>:<account-id>:managed-instance/<managed-instance-id>"
ROLES = {
    "plan": (PLAN_TRUST, PLAN_PERMISSIONS, "repo:amsyrzmi/CSAV2:environment:production-plan"),
    "apply": (APPLY_TRUST, APPLY_PERMISSIONS, "repo:amsyrzmi/CSAV2:environment:production"),
}


def _as_list(value: str | list[str]) -> list[str]:
    return value if isinstance(value, list) else [value]


def _policy(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _trust_allows(policy: dict[str, Any], claims: dict[str, str]) -> bool:
    """Whether a token with these claims may assume the role (``StringEquals`` only, as used)."""
    for statement in policy["Statement"]:
        conditions = statement["Condition"]
        if set(conditions) != {"StringEquals"}:
            return False
        if all(
            claims.get(key) in _as_list(want) for key, want in conditions["StringEquals"].items()
        ):
            return statement["Effect"] == "Allow"
    return False


@pytest.mark.parametrize("kind", ["plan", "apply"])
def test_trust_policy_is_scoped_to_this_repo_and_one_environment(kind: str) -> None:
    trust, _permissions, sub = ROLES[kind]
    policy = _policy(trust)

    assert set(policy) == {"Version", "Statement"}
    assert policy["Version"] == "2012-10-17"
    (statement,) = policy["Statement"]
    assert set(statement) == {"Effect", "Principal", "Action", "Condition"}
    assert statement["Effect"] == "Allow"
    assert statement["Principal"] == {"Federated": "<oidc-provider-arn>"}
    assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert statement["Condition"] == {"StringEquals": {AUD: "sts.amazonaws.com", SUB: sub}}
    assert "*" not in json.dumps(statement["Condition"])
    assert "*" not in json.dumps(statement["Principal"])


def test_each_trust_policy_admits_only_its_own_environment() -> None:
    plan, apply = _policy(PLAN_TRUST), _policy(APPLY_TRUST)
    plan_sub, apply_sub = ROLES["plan"][2], ROLES["apply"][2]

    def token(sub: str) -> dict[str, str]:
        return {AUD: "sts.amazonaws.com", SUB: sub}

    assert _trust_allows(plan, token(plan_sub))
    assert not _trust_allows(plan, token(apply_sub))
    assert _trust_allows(apply, token(apply_sub))
    assert not _trust_allows(apply, token(plan_sub))
    for policy in (plan, apply):
        for sub in (
            "repo:amsyrzmi/CSAV2:ref:refs/heads/main",
            "repo:amsyrzmi/CSAV2:pull_request",
            "repo:someone/CSAV2:environment:production",
        ):
            assert not _trust_allows(policy, token(sub)), sub
        assert not _trust_allows(policy, {SUB: plan_sub, AUD: "other"})


@pytest.mark.parametrize("kind", ["plan", "apply"])
def test_permissions_policy_has_only_the_allowed_actions(kind: str) -> None:
    _trust, permissions, _sub = ROLES[kind]
    policy = _policy(permissions)
    document_arn = f"arn:aws:ssm:<region>:<account-id>:document/{DOCUMENT_NAMES[kind]}"

    assert set(policy) == {"Version", "Statement"}
    assert policy["Version"] == "2012-10-17"
    statements = policy["Statement"]
    for statement in statements:
        assert set(statement) <= {"Sid", "Effect", "Action", "Resource"}
        assert statement["Effect"] == "Allow"
    actions = [action for statement in statements for action in _as_list(statement["Action"])]
    assert sorted(actions) == ["ssm:GetCommandInvocation", "ssm:SendCommand"]

    (send,) = [s for s in statements if "ssm:SendCommand" in _as_list(s["Action"])]
    assert _as_list(send["Action"]) == ["ssm:SendCommand"]
    assert sorted(_as_list(send["Resource"])) == sorted([document_arn, INSTANCE_ARN])
    assert "*" not in json.dumps(send)

    # GetCommandInvocation supports no resource-level scoping (the service authorization
    # reference lists no resource type for it), so it can only be granted on "*".
    (read,) = [s for s in statements if s is not send]
    assert _as_list(read["Action"]) == ["ssm:GetCommandInvocation"]
    assert read["Resource"] == "*"


def test_each_permissions_policy_names_only_its_own_document() -> None:
    plan_text, apply_text = PLAN_PERMISSIONS.read_text(), APPLY_PERMISSIONS.read_text()

    assert "document/csa-production-plan" in plan_text
    assert "csa-production-apply" not in plan_text
    assert "document/csa-production-apply" in apply_text
    assert "csa-production-plan" not in apply_text
    for text in (plan_text, apply_text):
        assert "csa-production-deploy" not in text
        assert text.count(":document/") == 1


NEW_FILES = (
    SSM_RUN,
    PARSE_PLAN,
    PLAN_DOCUMENT,
    APPLY_DOCUMENT,
    PLAN_TRUST,
    APPLY_TRUST,
    PLAN_PERMISSIONS,
    APPLY_PERMISSIONS,
    WORKFLOW,
    PRODUCTION_DIR / "backup.sh",
)


@pytest.mark.parametrize("path", NEW_FILES, ids=lambda path: path.name)
def test_deploy_files_hold_no_account_instance_region_or_address(path: Path) -> None:
    text = path.read_text()

    assert not re.search(r"\b\d{12}\b", text)
    assert not re.search(r"\b(?:mi|i)-[0-9a-f]{8,17}\b", text)
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)
    assert not re.search(r"\b(?:us|eu|ap|sa|ca|me|af|il|mx)-[a-z]+-\d\b", text)
    assert not re.search(r"arn:aws:iam::\d", text)


# --- the deploy-production workflow -------------------------------------------------------------

JOB_PERMISSIONS = ["contents: read", "deployments: read", "id-token: write"]
PIN = re.compile(r"^\s*(?:- )?uses: ([\w.-]+/[\w.-]+)@[0-9a-f]{40} # v\d+\.\d+\.\d+$")


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _children(text: str, header: str, *, direct: bool = True) -> list[str]:
    """The stripped lines nested under the line ``header`` (exact, with its indent)."""
    lines = text.splitlines()
    start = lines.index(header)
    indent = len(header) - len(header.lstrip())
    nested: list[str] = []
    for line in lines[start + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        depth = len(line) - len(line.lstrip())
        if depth <= indent:
            break
        if not direct or depth == indent + 2:
            nested.append(line.strip())
    return nested


def _jobs() -> dict[str, str]:
    section = _workflow().split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([a-z][a-z0-9_-]*):\n", section, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def _steps(job: str) -> list[str]:
    body = _jobs()[job].split("\n    steps:\n", 1)[1]
    return re.split(r"^      - ", body, flags=re.MULTILINE)[1:]


def _step(job: str, name: str) -> str:
    (step,) = [step for step in _steps(job) if step.startswith(f"name: {name}\n")]
    return step


def _run_blocks(text: str) -> list[str]:
    """Every ``run:`` value: the dedented body of a ``run: |`` block, or a one-line command."""
    lines = text.splitlines()
    blocks: list[str] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)(?:- )?run:(.*)$", line)
        if not match:
            continue
        if match.group(2).strip() != "|":
            blocks.append(match.group(2).strip())
            continue
        body: list[str] = []
        for following in lines[index + 1 :]:
            if following.strip() and len(following) - len(following.lstrip()) <= len(
                match.group(1)
            ):
                break
            body.append(following)
        indent = min(len(item) - len(item.lstrip()) for item in body if item.strip())
        blocks.append("\n".join(item[indent:] for item in body).strip("\n") + "\n")
    return blocks


def _run_step(
    job: str, name: str, env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess[bytes]:
    (block,) = _run_blocks(_step(job, name))
    return subprocess.run(
        ["bash", "-c", block], env=env, cwd=cwd, capture_output=True, timeout=30, check=False
    )


def test_workflow_triggers_permissions_and_pinning() -> None:
    text = _workflow()
    top = [line for line in text.splitlines() if line and not line[0].isspace() and line[0] != "#"]
    assert top == ["name: deploy-production", "on:", "permissions:", "concurrency:", "jobs:"]
    assert _children(text, "on:") == ["workflow_dispatch:"]
    assert _children(text, "    inputs:") == ["sha:"]
    sha_input = _children(text, "      sha:")
    assert "required: false" in sha_input
    assert "type: string" in sha_input
    (description,) = [line for line in sha_input if line.startswith("description:")]
    assert "empty = newest staged commit" in description
    assert _children(text, "permissions:") == ["contents: read"]
    assert _children(text, "concurrency:") == [
        "group: deploy-production",
        "cancel-in-progress: false",
    ]
    assert text.count("concurrency:") == 1
    assert text.count("permissions:") == 3

    jobs = _jobs()
    assert list(jobs) == ["plan", "apply"]
    for name, body in jobs.items():
        assert _children("  job:\n" + body, "    permissions:") == JOB_PERMISSIONS, name
        assert "    runs-on: ubuntu-24.04\n" in body
        assert "    if: github.ref == 'refs/heads/main'\n" in body, name
    assert "    environment: production-plan\n" in jobs["plan"]
    assert "needs:" not in jobs["plan"]
    assert "    environment: production\n" in jobs["apply"]
    assert "    needs: plan\n" in jobs["apply"]

    uses = [line for line in text.splitlines() if re.match(r"^\s*(?:- )?uses:", line)]
    assert len(uses) == 4
    pinned = [PIN.match(line) for line in uses]
    assert all(pinned), uses
    assert sorted({match.group(1) for match in pinned if match}) == [
        "actions/checkout",
        "aws-actions/configure-aws-credentials",
    ]
    assert len({line.strip() for line in uses}) == 2  # both jobs use the same pins


def test_workflow_never_interpolates_inputs_into_run_text() -> None:
    text = _workflow()
    blocks = _run_blocks(text)

    assert len(blocks) == 6
    for block in blocks:
        assert block.startswith("set -euo pipefail\n"), block
        assert "${{" not in block, block
        assert _unquoted_expansions(block) == [], block
    outputs = {
        "sha: ${{ steps.parse.outputs.sha }}",
        "migrations: ${{ steps.parse.outputs.migrations }}",
        "host_digest: ${{ steps.parse.outputs.host_digest }}",
    }
    assert set(_children(text, "    outputs:")) == outputs
    for line in text.splitlines():
        # Secrets reach a step only through env: or an action's with: input.
        if "secrets." in line:
            assert re.fullmatch(
                r"(?:[A-Z][A-Z_]*|role-to-assume): \$\{\{ secrets\.[A-Z_]+ \}\}", line.strip()
            ), line
        if re.search(r"\$\{\{\s*(?:inputs|needs|steps)\.", line):
            stripped = line.strip()
            assert stripped in outputs or re.fullmatch(
                r"[A-Z][A-Z_]*: \$\{\{ (?:inputs|needs|steps)\.[a-z_.]+ \}\}", stripped
            ), line


def _variables_env(**overrides: str | None) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "ROLE_ARN": "arn:aws:iam::<account-id>:role/example",
        "AWS_REGION": REGION,
        "INSTANCE_ID": INSTANCE,
    }
    for name, value in overrides.items():
        if value is None:
            env.pop(name)
        else:
            env[name] = value
    return env


# Each job's role ARN secret lives on its own environment; the instance id secret on both.
ROLE_SECRETS = {"plan": "PROD_PLAN_ROLE_ARN", "apply": "PROD_APPLY_ROLE_ARN"}


@pytest.mark.parametrize("job", ["plan", "apply"])
def test_workflow_fails_closed_on_missing_variables(job: str, tmp_path: Path) -> None:
    text = _workflow()
    body = _jobs()[job]
    role, other_role = ROLE_SECRETS[job], ROLE_SECRETS["apply" if job == "plan" else "plan"]
    assert _children("  job:\n" + body, "    env:") == [
        f"ROLE_ARN: ${{{{ secrets.{role} }}}}",
        "AWS_REGION: ${{ vars.PROD_DEPLOY_REGION }}",
        "INSTANCE_ID: ${{ secrets.PROD_INSTANCE_ID }}",
    ]
    assert other_role not in body
    for gone in ("vars.PROD_DEPLOY_ROLE_ARN", "vars.PROD_INSTANCE_ID", "PROD_DEPLOY_ROLE_ARN"):
        assert gone not in text
    (configure,) = [step for step in _steps(job) if "configure-aws-credentials@" in step]
    assert f"role-to-assume: ${{{{ secrets.{role} }}}}" in configure
    assert "aws-region: ${{ vars.PROD_DEPLOY_REGION }}" in configure
    assert "mask-aws-account-id: true" in configure
    assert _steps(job)[0].startswith("name: Require the deploy variables\n")

    name = "Require the deploy variables"
    assert _run_step(job, name, _variables_env(), tmp_path).returncode == 0
    for variable, setting in [
        ("ROLE_ARN", role),
        ("AWS_REGION", "PROD_DEPLOY_REGION"),
        ("INSTANCE_ID", "PROD_INSTANCE_ID"),
    ]:
        for value in ("", None):
            result = _run_step(job, name, _variables_env(**{variable: value}), tmp_path)
            assert result.returncode != 0
            assert setting.encode() in result.stderr


@pytest.mark.parametrize("job", ["plan", "apply"])
def test_workflow_runs_the_scripts_from_a_main_checkout(job: str) -> None:
    text = _workflow()
    assert re.findall(r"^[ \t]*ref:.*$", text, flags=re.MULTILINE) == ["          ref: main"] * 2
    assert "working-directory:" not in text

    steps = _steps(job)
    (checkout,) = [i for i, step in enumerate(steps) if "uses: actions/checkout@" in step]
    assert _children("      - " + steps[checkout], "        with:") == [
        "ref: main",
        "persist-credentials: false",
    ]
    (configure,) = [i for i, step in enumerate(steps) if "configure-aws-credentials@" in step]
    scripts = [i for i, step in enumerate(steps) if "deploy/production/" in step]
    assert scripts
    assert checkout < configure < min(scripts)
    invoked = re.findall(r"deploy/production/\w+\.sh[^\n>|]*", "".join(steps))
    expected = {
        "plan": [
            'deploy/production/ssm_run.sh plan "$SHA" ',
            'deploy/production/parse_plan.sh "$RUNNER_TEMP/plan.txt" "$SHA" ',
        ],
        "apply": ['deploy/production/ssm_run.sh apply "$SHA" "$MIGRATIONS" "$HOST_DIGEST" '],
    }
    assert invoked == expected[job]


def _resolve(
    harness: Harness, input_sha: str, *, locale: str = "C"
) -> subprocess.CompletedProcess[bytes]:
    harness.gh_dir.mkdir(exist_ok=True)
    env = harness.env(
        INPUT_SHA=input_sha,
        GH_TOKEN="test-token",
        REPOSITORY="example/repo",
        RUNNER_TEMP=str(harness.root),
        GITHUB_OUTPUT=str(harness.root / "github-output"),
        STUB_GH_DIR=str(harness.gh_dir),
        LC_ALL=locale,
        LANG=locale,
    )
    return _run_step("plan", "Resolve the commit to deploy", env, harness.root)


def _deployments(harness: Harness, *deployments: tuple[object, str, list[str]]) -> None:
    harness.gh_dir.mkdir(exist_ok=True)
    listing = [{"id": number, "sha": sha} for number, sha, _states in deployments]
    (harness.gh_dir / "deployments.json").write_text(json.dumps(listing))
    for number, _sha, states in deployments:
        statuses = [{"state": state} for state in states]
        (harness.gh_dir / f"statuses-{number}.json").write_text(json.dumps(statuses))


def _github_output(harness: Harness) -> str:
    path = harness.root / "github-output"
    return path.read_text() if path.exists() else ""


def test_workflow_uses_a_valid_input_sha_without_asking_github(harness: Harness) -> None:
    result = _resolve(harness, SHA)

    assert result.returncode == 0, result.stderr
    assert _github_output(harness) == f"sha={SHA}\n"
    assert harness.calls() == []


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("value", [value for value in BAD_VALUES["sha"] if value])
def test_workflow_refuses_a_bad_input_sha(harness: Harness, value: str, locale: str) -> None:
    result = _resolve(harness, value, locale=locale)

    assert result.returncode != 0
    assert _github_output(harness) == ""
    assert harness.calls() == []


def test_workflow_resolves_the_newest_successful_staging_deployment(harness: Harness) -> None:
    newest, older, oldest = "a" * 40, "b" * 40, "c" * 40
    _deployments(
        harness,
        (3, newest, ["in_progress", "failure"]),
        (2, older, ["success", "inactive"]),
        (1, oldest, ["success"]),
    )
    result = _resolve(harness, "")

    assert result.returncode == 0, result.stderr
    assert _github_output(harness) == f"sha={older}\n"
    listing = "repos/example/repo/deployments?environment=staging&per_page=100"
    assert harness.calls() == [
        ["gh", "api", listing],
        ["gh", "api", "repos/example/repo/deployments/3/statuses?per_page=100"],
        ["gh", "api", "repos/example/repo/deployments/2/statuses?per_page=100"],
    ]


@pytest.mark.parametrize(
    "deployments",
    [
        pytest.param([], id="no-deployments"),
        pytest.param([(2, "a" * 40, ["failure"]), (1, "b" * 40, [])], id="none-succeeded"),
        pytest.param([("2; id", "a" * 40, ["success"])], id="malformed-id"),
        pytest.param([(2, "A" * 40, ["success"])], id="malformed-sha"),
    ],
)
def test_workflow_refuses_when_no_staged_commit_is_found(
    harness: Harness, deployments: list[tuple[object, str, list[str]]]
) -> None:
    _deployments(harness, *deployments)
    result = _resolve(harness, "")

    assert result.returncode != 0
    assert _github_output(harness) == ""


SSM_RUN_STUB = r"""#!/bin/bash
IFS=$'\t'
printf 'ssm_run\t%s\n' "$*" >> "$STUB_LOG"
printf '%s' "$STUB_SSM_OUTPUT"
exit "${STUB_SSM_STATUS:-0}"
"""


def _workspace(harness: Harness) -> Path:
    """A checkout with a stub ``ssm_run.sh`` and the real ``parse_plan.sh``."""
    work = harness.root / "work"
    scripts = work / "deploy" / "production"
    scripts.mkdir(parents=True)
    harness._write_stub(scripts / "ssm_run.sh", SSM_RUN_STUB)
    harness._write_stub(scripts / "parse_plan.sh", PARSE_PLAN.read_text())
    return work


@pytest.mark.parametrize("status", [0, 1])
def test_workflow_plan_summarises_the_plan_and_exposes_its_values(
    harness: Harness, status: int
) -> None:
    work = _workspace(harness)
    summary = harness.root / "summary.md"
    output = harness.root / "github-output"
    env = harness.env(
        SHA=SHA,
        RUNNER_TEMP=str(harness.root),
        GITHUB_STEP_SUMMARY=str(summary),
        GITHUB_OUTPUT=str(output),
        STUB_SSM_OUTPUT=GOOD_PLAN.rstrip("\n"),
        STUB_SSM_STATUS=str(status),
    )
    planned = _run_step("plan", "Plan on the production instance", env, work)

    assert planned.returncode == status
    assert harness.calls() == [["ssm_run", "plan", SHA]]
    assert planned.stdout.decode() == GOOD_PLAN.rstrip("\n")
    assert summary.read_text() == (f"### Production plan for `{SHA}`\n\n````\n{GOOD_PLAN}````\n")
    if status:
        return

    parsed = _run_step("plan", "Read the plan", env, work)
    assert parsed.returncode == 0, parsed.stderr
    assert output.read_bytes() == _outputs(SHA, "0486,0487", PLAN_DIGEST)


@pytest.mark.parametrize("migrations", ["", MIGRATIONS])
def test_workflow_apply_sends_exactly_the_planned_values(harness: Harness, migrations: str) -> None:
    work = _workspace(harness)
    summary = harness.root / "summary.md"
    env = harness.env(
        SHA=SHA,
        MIGRATIONS=migrations,
        HOST_DIGEST=DIGEST,
        RUNNER_TEMP=str(harness.root),
        GITHUB_STEP_SUMMARY=str(summary),
        STUB_SSM_OUTPUT="applied\n",
        STUB_SSM_STATUS="3",
    )
    result = _run_step("apply", "Apply on the production instance", env, work)

    assert result.returncode == 3
    assert harness.calls() == [["ssm_run", "apply", SHA, migrations, DIGEST]]
    assert result.stdout == b"applied\n"
    assert summary.read_text() == f"### Production apply of `{SHA}`\n\n````\napplied\n````\n"


# --- backup.sh: the instance's backup, kept in the repository ------------------------------------

BACKUP = PRODUCTION_DIR / "backup.sh"
BACKUP_HOST_ENV = "HOST_ENV=/srv/csa-prod/host.env"
BACKUP_LAST_PARSE = "REGION=$(host_value CSA_PROD_S3_REGION)"
BUCKET_VALUE = "bucket-value-x"
REGION_VALUE = "region-value-x"


def test_backup_script_is_bash_strict_and_has_no_secrets() -> None:
    text = BACKUP.read_text()
    lines = text.splitlines()

    assert lines[0] in {"#!/bin/bash", "#!/usr/bin/env bash"}
    assert "set -euo pipefail" in lines
    assert "exec 9>/var/tmp/csa-backup.lock" in lines
    assert any(line.startswith("flock -n 9 ") for line in lines)
    assert not re.search(r"AKIA[0-9A-Z]{16}", text)
    assert BACKUP.stat().st_mode & stat.S_IXUSR
    mode = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-s", "--", str(BACKUP)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert mode.startswith("100755 ")


def test_backup_script_names_no_bucket_or_region() -> None:
    text = BACKUP.read_text()

    # The public repository holds neither: both come from the instance's host file.
    assert "fourstacks" not in text.lower()
    assert "ap-southeast-1" not in text
    assert not re.search(r"\b(?:us|eu|ap|sa|ca|me|af|il|mx)-[a-z]+-\d\b", text)
    assert re.findall(r"s3://\S*", text) == ['s3://$B/backups/csa_staging-$ts.dump"']
    assert "B=$(host_value CSA_PROD_S3_BUCKET)" in text.splitlines()
    assert BACKUP_LAST_PARSE in text.splitlines()
    assert 'AWS_DEFAULT_REGION="$REGION"' in text
    assert not re.search(r"\bsource\b|^\s*\.\s", text, re.MULTILINE)


def test_backup_script_keeps_the_live_steps_in_order() -> None:
    lines = BACKUP.read_text().splitlines()
    steps = [
        "LOG=/srv/csa-prod/backup/backup.log",
        "exec 9>/var/tmp/csa-backup.lock",
        'flock -n 9 || { echo "$(date -u +%Y%m%dT%H%M%SZ) skipped: previous backup still running"'
        ' >> "$LOG"; exit 0; }',
        "renice -n 19 -p $$ > /dev/null",
        "ionice -c3 -p $$ || true",
        '[ "$(df --output=avail -k /var/tmp | tail -1)" -gt 2097152 ] || '
        '{ echo "less than 2 GB free in /var/tmp" >&2; exit 1; }',
        BACKUP_HOST_ENV,
        "B=$(host_value CSA_PROD_S3_BUCKET)",
        BACKUP_LAST_PARSE,
        "read -r AK SK < /srv/csa-prod/backup/s3-key.txt",
        'export AWS_ACCESS_KEY_ID="$AK" AWS_SECRET_ACCESS_KEY="$SK" AWS_DEFAULT_REGION="$REGION"',
        "ts=$(date -u +%Y%m%dT%H%M%SZ)",
        "tmp=$(mktemp /var/tmp/csa-dump.XXXXXX); trap 'rm -f \"$tmp\"' EXIT",
        "docker exec csa-prod-staging-db-1 nice -n 19 pg_dump -U csa -d csa_staging -Fc -Z 3"
        ' > "$tmp"',
        'docker exec -i csa-prod-staging-db-1 pg_restore --list < "$tmp" > /dev/null',
        'aws s3 cp "$tmp" "s3://$B/backups/csa_staging-$ts.dump" --only-show-errors',
        '[ -s /srv/csa-prod/backup/ping-url ] && curl -fsS -m 10 "$(cat /srv/csa-prod/backup/'
        'ping-url)" > /dev/null || true',
        'echo "$ts ok $(stat -c %s "$tmp") bytes" >> "$LOG"',
    ]
    positions = [lines.index(step) for step in steps]
    assert positions == sorted(positions)
    assert lines[-1] == steps[-1]


def _host_parse_block(host_env: Path) -> str:
    """The script's host-file lines, pointed at ``host_env``; the rest of backup.sh uses fixed
    instance paths (the lock, the key file, the log), so only these lines are run here."""
    lines = BACKUP.read_text().splitlines()
    start, end = lines.index(BACKUP_HOST_ENV), lines.index(BACKUP_LAST_PARSE)
    block = [f"HOST_ENV={host_env}", *lines[start + 1 : end + 1]]
    return "\n".join(["set -euo pipefail", *block, 'printf "%s|%s\\n" "$B" "$REGION"', ""])


def _run_host_parse(
    tmp_path: Path, host_text: str | None, locale: str = "C"
) -> subprocess.CompletedProcess[str]:
    host_env = tmp_path / "host.env"
    if host_text is not None:
        host_env.write_text(host_text)
    script = tmp_path / "parse.sh"
    script.write_text(_host_parse_block(host_env))
    return subprocess.run(
        ["bash", str(script)],
        env={"PATH": "/usr/bin:/bin", "LC_ALL": locale, "LANG": locale},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    "host_text",
    [
        f"CSA_PROD_S3_BUCKET={BUCKET_VALUE}\nCSA_PROD_S3_REGION={REGION_VALUE}\n",
        "# instance settings\n"
        "CSA_STAGING_CONSOLE_ORIGIN=https://console.example.test\n"
        f"export CSA_PROD_S3_BUCKET={BUCKET_VALUE}\n"
        f"export\tCSA_PROD_S3_REGION = {REGION_VALUE}  \n"
        "CSA_PROD_MEM_DB=1000m\n",
    ],
    ids=["plain", "export-comments-and-other-keys"],
)
def test_backup_reads_bucket_and_region_from_the_host_file(tmp_path: Path, host_text: str) -> None:
    result = _run_host_parse(tmp_path, host_text)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{BUCKET_VALUE}|{REGION_VALUE}\n"


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize(
    ("host_text", "key"),
    [
        (f"CSA_PROD_S3_REGION={REGION_VALUE}\n", "CSA_PROD_S3_BUCKET"),
        (f"CSA_PROD_S3_BUCKET={BUCKET_VALUE}\n", "CSA_PROD_S3_REGION"),
        (f"CSA_PROD_S3_BUCKET=\nCSA_PROD_S3_REGION={REGION_VALUE}\n", "CSA_PROD_S3_BUCKET"),
        (f"CSA_PROD_S3_BUCKET={BUCKET_VALUE}\nCSA_PROD_S3_REGION=  \n", "CSA_PROD_S3_REGION"),
        (
            f"# CSA_PROD_S3_BUCKET={BUCKET_VALUE}\nCSA_PROD_S3_REGION={REGION_VALUE}\n",
            "CSA_PROD_S3_BUCKET",
        ),
        (f"CSA_PROD_S3_BUCKET=a;id\nCSA_PROD_S3_REGION={REGION_VALUE}\n", "CSA_PROD_S3_BUCKET"),
        (f'CSA_PROD_S3_BUCKET="b"\nCSA_PROD_S3_REGION={REGION_VALUE}\n', "CSA_PROD_S3_BUCKET"),
        (f"CSA_PROD_S3_BUCKET=a b\nCSA_PROD_S3_REGION={REGION_VALUE}\n", "CSA_PROD_S3_BUCKET"),
        (f"CSA_PROD_S3_BUCKET=$(id)\nCSA_PROD_S3_REGION={REGION_VALUE}\n", "CSA_PROD_S3_BUCKET"),
        (f"CSA_PROD_S3_BUCKET=a/b\nCSA_PROD_S3_REGION={REGION_VALUE}\n", "CSA_PROD_S3_BUCKET"),
        (f"CSA_PROD_S3_BUCKET={BUCKET_VALUE}\nCSA_PROD_S3_REGION=r٠\n", "CSA_PROD_S3_REGION"),
        (f"CSA_PROD_S3_BUCKET={BUCKET_VALUE}\nCSA_PROD_S3_REGION=rａ\n", "CSA_PROD_S3_REGION"),
        (
            f"CSA_PROD_S3_BUCKET={BUCKET_VALUE}\nCSA_PROD_S3_BUCKET={BUCKET_VALUE}\n"
            f"CSA_PROD_S3_REGION={REGION_VALUE}\n",
            "CSA_PROD_S3_BUCKET",
        ),
    ],
    ids=[
        "no-bucket",
        "no-region",
        "empty-bucket",
        "blank-region",
        "commented-bucket",
        "semicolon",
        "quoted",
        "space",
        "substitution",
        "slash",
        "arabic-indic-digit",
        "fullwidth-letter",
        "duplicate",
    ],
)
def test_backup_refuses_a_missing_empty_or_unsafe_host_value(
    tmp_path: Path, host_text: str, key: str, locale: str
) -> None:
    result = _run_host_parse(tmp_path, host_text, locale)

    assert result.returncode == 1
    assert result.stdout == ""
    assert key in result.stderr
    for value in (BUCKET_VALUE, REGION_VALUE, "a;id", "$(id)", "a/b", "r٠", "rａ"):
        assert value not in result.stderr


def test_backup_refuses_an_unreadable_host_file(tmp_path: Path) -> None:
    result = _run_host_parse(tmp_path, None)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "host.env" in result.stderr


# --- the runbook and the skills -----------------------------------------------------------------

PRODUCTION_DOC = ROOT / "deploy" / "PRODUCTION.md"
PIPELINE_DOC = ROOT / "deploy" / "STAGING_PIPELINE.md"
STAGING_SKILL = ROOT / ".claude" / "skills" / "staging-rollout" / "SKILL.md"
PRODUCTION_SKILL = ROOT / ".claude" / "skills" / "production-deploy" / "SKILL.md"


def _section(text: str, heading: str) -> str:
    """The level-two section whose heading starts with ``heading``, up to the next one."""
    lines = text.splitlines()
    starts = [n for n, line in enumerate(lines) if line.startswith(f"## {heading}")]
    assert len(starts) == 1, f"expected one '## {heading}' heading, found {len(starts)}"
    start = starts[0]
    end = next((n for n in range(start + 1, len(lines)) if lines[n].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def _production_doc() -> str:
    return PRODUCTION_DOC.read_text(encoding="utf-8")


def test_production_doc_mentions_every_one_time_step() -> None:
    text = _production_doc()
    deploying = _section(text, "Deploying from GitHub")
    setup = _section(text, "One-time setup")
    break_glass = _section(text, "Break-glass")

    assert "outside the staged-first rule" in break_glass
    assert "owner" in break_glass
    for needle in (
        "ssm-document-plan.json",
        "ssm-document-apply.json",
        "csa-production-plan",
        "csa-production-apply",
        "plan-trust-policy.json",
        "plan-permissions-policy.json",
        "apply-trust-policy.json",
        "apply-permissions-policy.json",
        "PROD_PLAN_ROLE_ARN",
        "PROD_APPLY_ROLE_ARN",
        "PROD_INSTANCE_ID",
        "PROD_DEPLOY_REGION",
        "production-plan",
        "`main`",
        "/srv/csa-prod/host.env",
        "DanSan0408",
        "amsyrzmi",
        "Prevent self-review",
        "AmazonSSMManagedInstanceCore",
        "/usr/local/sbin/csa-prod-deploy",
        "/opt/csa-deploy/venv",
        "deploy/production/requirements.txt",
        "/var/log/csa-prod-deploy",
        "read -rsp",
        sr.BACKUP_REINSTALL,
        "/srv/csa-prod/backup/backup.log",
    ):
        assert needle in setup, needle
    # host.env must exist with the bucket and region before the repo backup.sh replaces the
    # installed one, or the 6-hourly cron backup fails.
    assert setup.index("CSA_PROD_S3_BUCKET") < setup.index(sr.BACKUP_REINSTALL)
    for key in ("CSA_PROD_S3_REGION", "CSA_STAGING_CONSOLE_ORIGIN", "CSA_STAGING_HOOKS_ORIGIN"):
        assert key in setup, key
    for needle in (
        "deploy-production",
        "has not been rolled out to staging",
        sr.HOST_CHANGED,
        "is older than the running",
        sr.BACKUP_REINSTALL,
        "skipped",
        "124",
        "75",
        sr.RECORD_COMMAND,
    ):
        assert needle in deploying, needle


def test_production_doc_explains_restoring_the_named_backup() -> None:
    restore = _section(_production_doc(), "Restoring a backup")

    for needle in (
        "Backup taken: backups/csa_staging-",
        "csa-prod-staging-db-1",
        "csa_staging",
        "-U csa",
        "pg_restore",
        "put-only",
        "DROP DATABASE",
    ):
        assert needle in restore, needle
    # The scratch restore comes before the live one, which is the owner's deliberate action.
    assert restore.index("csa_restore_check") < restore.index("DROP DATABASE IF EXISTS csa_staging")


def test_setup_section_names_every_secret_variable_and_environment_the_workflow_reads() -> None:
    workflow = _workflow()
    setup = _section(_production_doc(), "One-time setup")
    names = set(re.findall(r"\$\{\{ (?:secrets|vars)\.([A-Z0-9_]+) \}\}", workflow))
    environments = set(re.findall(r"^\s+environment: ([\w-]+)$", workflow, re.MULTILINE))

    assert names == {
        "PROD_PLAN_ROLE_ARN",
        "PROD_APPLY_ROLE_ARN",
        "PROD_INSTANCE_ID",
        "PROD_DEPLOY_REGION",
    }
    assert environments == {"production", "production-plan"}
    for name in names | environments:
        assert name in setup, name


SCANNED_DOCS = ("PRODUCTION.md", "production skill", "staging skill", "pipeline routine")


def _scanned_doc(name: str) -> str:
    """The new and changed doc text: whole files, and the one changed section of the pipeline."""
    if name == "pipeline routine":
        return _section(PIPELINE_DOC.read_text(encoding="utf-8"), "Routine rollouts")
    path = {
        "PRODUCTION.md": PRODUCTION_DOC,
        "production skill": PRODUCTION_SKILL,
        "staging skill": STAGING_SKILL,
    }[name]
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", SCANNED_DOCS)
def test_docs_contain_no_account_ids_ip_addresses_or_arns(name: str) -> None:
    text = _scanned_doc(name)

    assert not re.search(r"\b\d{12}\b", text)
    assert not re.search(r"arn:aws:[a-z0-9-]*:[a-z0-9-]*:\d", text)
    assert not re.search(r"\b(?:mi|i)-[0-9a-f]{8,17}\b", text)
    assert not re.search(r"\b(?:us|eu|ap|sa|ca|me|af|il|mx)-[a-z]+-\d\b", text)
    # Loopback (the readiness probe) identifies nothing; any other address does.
    addresses = re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)
    assert [address for address in addresses if not address.startswith("127.")] == []
    assert "fourstacks.my" not in text
    assert "fourstacks-csa-prod" not in text
    assert not re.search(r"\b(?:mito|webhooks)\.[a-z]", text)


def test_the_scan_catches_what_it_forbids() -> None:
    samples = (
        "123456789012",
        "arn:aws:iam::123456789012:role/x",
        "mi-0123456789abcdef0",
        "10.1.2.3",
        "ap-southeast-1",
    )
    patterns = (
        r"\b\d{12}\b",
        r"arn:aws:[a-z0-9-]*:[a-z0-9-]*:\d",
        r"\b(?:mi|i)-[0-9a-f]{8,17}\b",
        r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
        r"\b(?:us|eu|ap|sa|ca|me|af|il|mx)-[a-z]+-\d\b",
    )
    for sample, pattern in zip(samples, patterns, strict=True):
        assert re.search(pattern, sample), sample


def test_staging_skill_mentions_the_staged_record() -> None:
    retry = f"{sr.RECORD_COMMAND} --sha"
    skill = STAGING_SKILL.read_text(encoding="utf-8")
    routine = _section(PIPELINE_DOC.read_text(encoding="utf-8"), "Routine rollouts")

    for text in (skill, routine):
        assert retry in text
        assert "GitHub Deployment" in text
        assert "`staging`" in text
        assert "production" in text


def test_production_skill_plans_waits_for_approval_and_never_bypasses_the_gate() -> None:
    text = PRODUCTION_SKILL.read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]

    assert "name: production-deploy" in frontmatter
    assert "description: Use when the owner asks to deploy" in frontmatter
    for needle in (
        "deploy-production",
        "gh workflow run deploy-production",
        "production-plan",
        "`production`",
        "has not been rolled out to staging",
        "never approve",
        "break-glass",
        "verbatim",
        "numbered, copy-ready commands",
        "deploy/PRODUCTION.md",
    ):
        assert needle in text, needle
    assert text.index("plan") < text.index("apply")


def test_production_doc_review_fixes() -> None:
    text = _production_doc()
    deploying = _section(text, "Deploying from GitHub")
    setup = _section(text, "One-time setup")

    # (a) a Systems Manager timeout is a terminal status: runner exit 1, not 124.
    assert "`TimedOut`" in deploying
    assert "40 minutes" in deploying
    assert "30 minutes" not in text
    # The runner's wait is 35 minutes of elapsed time since the send, not an estimate from polls.
    assert "35 minutes (by the clock, not a count of polls)" in deploying
    assert "about 35 minutes" not in text
    # (b) a fresh CloudShell tab fails before any placeholder is filled with nothing.
    guard = ': "${REGION:?}" "${ACCOUNT:?}" "${NODE:?}" "${OIDC:?}"'
    assert guard in setup
    assert setup.index(guard) < setup.index("sed -i") < setup.index("grep -n '<'")
    # (c) install replaces the file, so the directory must be writable by ubuntu.
    assert "sudo chown ubuntu:ubuntu /srv/csa-prod/backup /srv/csa-prod/backup/backup.sh" in setup
    assert setup.index("sudo chown ubuntu:ubuntu") < setup.index(sr.BACKUP_REINSTALL)
    # (d) the environments exist before their secrets are set.
    github = setup[setup.index("### 6.") : setup.index("### 7.")]
    assert "404" in github
    assert github.index("web settings") < github.index("gh secret set")
    # the activation code's exposure.
    assert "single-use" in setup
