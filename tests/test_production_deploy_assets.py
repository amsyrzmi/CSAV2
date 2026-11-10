"""Production deploy assets: the root-owned entry script, the Systems Manager document, the pins.

The entry script is run for real with ``bash`` against stub ``git`` and stub python executables
that append their argv (tab-separated, one invocation per line) to a file, so these tests assert
the exact commands the script would run without touching a repository or a database.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DIR = ROOT / "deploy" / "production"
ENTRY = PRODUCTION_DIR / "deploy_entry.sh"
DOCUMENT = PRODUCTION_DIR / "ssm-document.json"
REQUIREMENTS = PRODUCTION_DIR / "requirements.txt"

SHA = "0123456789abcdef0123456789abcdef01234567"
MIGRATIONS = "0486,0487"
DIGEST = "0123456789ab"

PARAMETERS = ("action", "sha", "migrations", "hostDigest")
EXPECTED_PATTERNS = {
    "action": "^(plan|apply)$",
    "sha": "^[0-9a-f]{40}$",
    "migrations": "^([0-9]{4}(,[0-9]{4})*)?$",
    "hostDigest": "^([0-9a-f]{12})?$",
}

# One table of values that must be refused, keyed by Systems Manager parameter name. The entry
# script tests and the document pattern tests both read it, so the two layers cannot drift apart.
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
# Refusals that only the entry script can make: the digest is required for apply and empty for plan.
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


class Harness:
    """Stub executables and an environment the entry script can run in."""

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
        self._write_stub(self.bin / "git", GIT_STUB)
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
        return subprocess.run(
            ["bash", str(ENTRY), *args],
            env=self.env(**extra_env),
            input=stdin,
            capture_output=True,
            timeout=30,
            check=False,
        )

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


def _document() -> dict[str, object]:
    return json.loads(DOCUMENT.read_text())


def test_document_parameters_have_the_exact_patterns_and_the_one_command() -> None:
    document = _document()

    assert document["schemaVersion"] == "2.2"
    parameters = document["parameters"]
    assert isinstance(parameters, dict)
    assert list(parameters) == list(PARAMETERS)
    for name, pattern in EXPECTED_PATTERNS.items():
        assert parameters[name]["type"] == "String"
        assert parameters[name]["description"]
        assert parameters[name]["allowedPattern"] == pattern
    assert parameters["migrations"]["default"] == ""
    assert parameters["hostDigest"]["default"] == ""
    assert "default" not in parameters["action"]
    assert "default" not in parameters["sha"]

    steps = document["mainSteps"]
    assert isinstance(steps, list)
    assert len(steps) == 1
    step = steps[0]
    assert step["action"] == "aws:runShellScript"
    assert step["name"] == "deploy"
    assert step["inputs"]["timeoutSeconds"] == "1800"
    commands = step["inputs"]["runCommand"]
    assert commands == [
        "set -eu",
        "exec runuser -u ubuntu -- /usr/local/sbin/csa-prod-deploy "
        '"{{ action }}" "{{ sha }}" "{{ migrations }}" "{{ hostDigest }}"',
    ]

    command = commands[1]
    placeholders = re.findall(r"\{\{[^}]*\}\}", command)
    names = [re.fullmatch(r"\{\{ (\w+) \}\}", item) for item in placeholders]
    assert all(names)
    assert [match.group(1) for match in names if match] == list(PARAMETERS)
    assert len(re.findall(r'"\{\{ \w+ \}\}"', command)) == len(placeholders)
    # Nothing else in the document may smuggle in a placeholder or another command.
    assert document["description"]
    assert "{{" not in json.dumps({k: v for k, v in document.items() if k != "mainSteps"})


def test_document_patterns_reject_injection() -> None:
    parameters = _document()["parameters"]
    assert isinstance(parameters, dict)

    for name, values in BAD_VALUES.items():
        pattern = parameters[name]["allowedPattern"]
        for value in values:
            assert re.fullmatch(pattern, value) is None, (name, value)
    for name, value in DEFAULTS.items():
        assert re.fullmatch(parameters[name]["allowedPattern"], value), (name, value)
    assert re.fullmatch(parameters["migrations"]["allowedPattern"], "")
    assert re.fullmatch(parameters["hostDigest"]["allowedPattern"], "")


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
