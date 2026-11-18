"""deploy/staging/rollout_gate.sh and record_staged.sh: the staging workflow's decision and record.

`rollout_gate.sh start` wraps deploy/production/resolve_commit.sh (run with DEPLOY_TARGET=staging)
and turns its answer into `proceed` and `reason` for the workflow. `rollout_gate.sh current` says
whether main is still at the planned commit. `record_staged.sh` writes the staged record with
the workflow token. All of them run against stub `gh` and `curl` executables on PATH.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from scripts import staging_rollout as sr
from scripts.rollout_targets import STAGING

from tests.test_production_deploy_assets import _unquoted_expansions
from tests.test_production_resolve import (
    FAILED_CHECKS,
    FE_DIGEST,
    GH_TOKEN,
    IMAGE_NAMESPACE,
    LOGIN_TOKEN,
    NOT_READY,
    OLDER,
    OTHER_REFUSALS,
    PY_DIGEST,
    REGISTRY_TOKEN,
    REPOSITORY,
    SHA7,
    STAGED_ON,
    TARGET_MESSAGE,
    TIP,
    Harness,
)

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "deploy" / "staging" / "rollout_gate.sh"
RECORD = ROOT / "deploy" / "staging" / "record_staged.sh"
RETRY_LINE = (
    "staging is fine but the staged record was not written; use Re-run failed jobs on this run"
)
DIGEST = "sha256:" + "ab" * 32
SHA_MESSAGE = "sha must be the full 40 character lowercase commit SHA"

# `gh api <path> --jq .sha` (the `current` command) and `gh api --method POST <path> --input -`
# (the record). Each call is logged; a POST body goes to $POST_LOG as `<path><TAB><compact json>`.
# A fail-gh-<key> file makes that call exit 1 after printing a line that holds the token (followed
# by the file's own text, e.g. ` (HTTP 403)`), so a test can prove the scripts never show gh's own
# stderr beyond an HTTP code.
GH_STUB = r"""#!/bin/bash
IFS=$'\t'
printf 'gh\t%s\n' "$*" >> "$STUB_LOG"
IFS=' '
if [[ "$1" == api && $# -eq 4 && "$3" == --jq && "$4" == .sha && "$2" == */commits/main ]]; then
    key=tip
elif [[ "$1" == api && $# -eq 6 && "$2" == --method && "$3" == POST \
    && "$5" == --input && "$6" == - ]]; then
    body="$(cat)"
    printf '%s\t%s\n' "$4" "$(jq -c . <<< "$body")" >> "$POST_LOG"
    case "$4" in
        */deployments) key=post-deployment ;;
        */deployments/*/statuses) key=post-status ;;
        *) exit 2 ;;
    esac
elif [[ "$1" == api && $# -eq 2 ]]; then
    # resolve_commit.sh's own reads: the same answers as the resolver's stub.
    case "$2" in
        */commits/main) key=commits ;;
        */compare/*) key=compare ;;
        */actions/workflows/ci.yml/runs\?head_sha=*\&branch=main\&event=push) key=runs ;;
        */deployments\?environment=staging\&sha=*\&per_page=100) key=deployments ;;
        */deployments/*/statuses\?per_page=100)
            id="${2%/statuses*}"
            key="statuses-${id##*/}"
            ;;
        *) exit 2 ;;
    esac
else
    exit 2
fi
if [[ -e "$STUB_DIR/fail-gh-$key" ]]; then
    echo "gh: stub failure for $key (token $GH_TOKEN)$(cat "$STUB_DIR/fail-gh-$key")" >&2
    exit 1
fi
cat "$STUB_DIR/$key.json"
"""

DEPLOYMENT_ID = 4242


class Gate(Harness):
    """The resolver's stub harness, plus this file's `gh` stub and a runner for the two scripts."""

    def __init__(self, tmp_path: Path) -> None:
        """Swap in a `gh` stub that also answers `current` and the record POSTs."""
        super().__init__(tmp_path)
        gh = self.bin / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        self.post_log = tmp_path / "posts.log"
        self.set("tip.json", TIP + "\n")
        self.set("post-deployment.json", json.dumps({"id": DEPLOYMENT_ID}))
        self.set("post-status.json", json.dumps({"id": 1}))

    def posts(self) -> list[tuple[str, dict[str, object]]]:
        """The POSTs so far, as (path, body) pairs."""
        if not self.post_log.exists():
            return []
        posts = []
        for line in self.post_log.read_text().splitlines():
            path, body = line.split("\t")
            posts.append((path, json.loads(body)))
        return posts

    def gh_calls(self) -> list[str]:
        """The argv of every gh call so far."""
        return [call[1] for call in self.calls() if call[0] == "gh"]

    def env(
        self,
        *,
        token: str | None = GH_TOKEN,
        repository: str | None = REPOSITORY,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """The environment the workflow gives the scripts, with the stubs first on PATH."""
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "STUB_DIR": str(self.dir),
            "STUB_LOG": str(self.log),
            "STUB_STDIN_LOG": str(self.stdin_log),
            "POST_LOG": str(self.post_log),
            "IMAGE_NAMESPACE": IMAGE_NAMESPACE,
            # The gate sets the target itself: a stale value in the environment must not win.
            "DEPLOY_TARGET": "production",
            **(extra or {}),
        }
        if token is not None:
            env["GH_TOKEN"] = token
        if repository is not None:
            env["REPOSITORY"] = repository
        return env

    def run_script(
        self,
        script: Path,
        *args: str,
        env: dict[str, str] | None = None,
        xtrace: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run a script with the given environment (default: the workflow's)."""
        return subprocess.run(
            ["bash", *(["-x"] if xtrace else []), str(script), *args],
            env=self.env() if env is None else env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )

    def start(
        self,
        event: str | None = "workflow_run",
        input_sha: str | None = "",
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """`rollout_gate.sh start` with EVENT_NAME and INPUT_SHA set (None leaves one unset)."""
        base = self.env() if env is None else env
        if event is not None:
            base = {**base, "EVENT_NAME": event}
        if input_sha is not None:
            base = {**base, "INPUT_SHA": input_sha}
        return self.run_script(GATE, "start", env=base)


@pytest.fixture
def gate(tmp_path: Path) -> Gate:
    return Gate(tmp_path)


def _keys(stdout: str) -> dict[str, str]:
    """Parse `key=value` lines, refusing a repeated key or a line without one."""
    found: dict[str, str] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        assert separator == "=", stdout
        assert key not in found, stdout
        found[key] = value
    return found


def _staged_nowhere(gate: Gate) -> None:
    """No deployment of the tip exists, so the tip is not staged."""
    gate.set("deployments.json", "[]")


# --- rollout_gate.sh start --------------------------------------------------------------------


def test_start_proceeds_for_a_ready_tip_and_prints_all_keys(gate: Gate) -> None:
    _staged_nowhere(gate)
    result = gate.start("workflow_run")
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"sha={TIP}\npython_digest={PY_DIGEST}\nfrontend_digest={FE_DIGEST}\n"
        f"staged_on=\nexplicit=false\nproceed=true\nreason=rolling out {SHA7}\n"
    )
    assert result.stderr == ""
    # The resolver ran for staging whatever the environment said, and nothing was written.
    assert gate.posts() == []
    assert not any("--method" in call for call in gate.gh_calls())


def test_start_on_workflow_run_skips_an_already_staged_tip(gate: Gate) -> None:
    result = gate.start("workflow_run")
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"sha={TIP}\npython_digest={PY_DIGEST}\nfrontend_digest={FE_DIGEST}\n"
        f"staged_on={STAGED_ON}\nexplicit=false\nproceed=false\n"
        f"reason=main's tip {SHA7} is already staged\n"
    )
    assert result.stderr == ""


@pytest.mark.parametrize("input_sha", ["", TIP])
def test_start_on_dispatch_rolls_out_an_already_staged_commit_again(
    gate: Gate, input_sha: str
) -> None:
    result = gate.start("workflow_dispatch", input_sha)
    assert result.returncode == 0, result.stderr
    keys = _keys(result.stdout)
    assert keys["staged_on"] == STAGED_ON
    assert keys["explicit"] == ("true" if input_sha else "false")
    assert keys["proceed"] == "true"
    assert keys["reason"] == f"rolling out {SHA7}"
    assert list(keys) == [
        "sha",
        "python_digest",
        "frontend_digest",
        "staged_on",
        "explicit",
        "proceed",
        "reason",
    ]


def test_start_passes_an_explicit_input_to_the_resolver_on_either_event(gate: Gate) -> None:
    # On workflow_run an explicit sha is still resolved as explicit (the workflow never sends one).
    result = gate.start("workflow_run", TIP)
    assert result.returncode == 0, result.stderr
    assert _keys(result.stdout)["explicit"] == "true"
    assert not any(call.endswith("/commits/main") for call in gate.gh_calls())


NOT_READY_CASES = [name for name in FAILED_CHECKS if name in NOT_READY]


@pytest.mark.parametrize("name", NOT_READY_CASES)
def test_start_on_workflow_run_turns_not_ready_into_nothing_to_roll_out(
    gate: Gate, name: str
) -> None:
    overrides, message = FAILED_CHECKS[name]
    for file, content in overrides.items():
        gate.set(file, content)
    result = gate.start("workflow_run")
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"proceed=false\nreason=nothing to roll out: {message}\n"
    assert result.stderr == ""


@pytest.mark.parametrize("name", NOT_READY_CASES)
def test_start_on_dispatch_fails_when_not_ready(gate: Gate, name: str) -> None:
    overrides, message = FAILED_CHECKS[name]
    for file, content in overrides.items():
        gate.set(file, content)
    for input_sha in ("", TIP):
        result = gate.start("workflow_dispatch", input_sha)
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == message + "\n"


OTHER_CASES = [
    name
    for name in (*FAILED_CHECKS, *OTHER_REFUSALS)
    # `not-staged` is production's refusal; staging reports an empty staged_on instead.
    if name not in NOT_READY and name != "not-staged"
]


@pytest.mark.parametrize("event", ["workflow_run", "workflow_dispatch"])
@pytest.mark.parametrize("name", OTHER_CASES)
def test_start_fails_closed_on_any_other_refusal_with_nothing_on_stdout(
    gate: Gate, name: str, event: str
) -> None:
    if name in FAILED_CHECKS:
        overrides, flags, args = FAILED_CHECKS[name][0], [], ""
    else:
        overrides, flags, argv = OTHER_REFUSALS[name]
        args = argv[0] if argv else ""
    for file, content in overrides.items():
        gate.set(file, content)
    for flag in flags:
        gate.flag(flag)
    result = gate.start(event, args)
    assert result.returncode == 1, result.stderr
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1, result.stderr
    assert GH_TOKEN not in result.stderr
    assert "stub failure" not in result.stderr


def test_start_reports_the_resolver_message_verbatim_on_a_refusal(gate: Gate) -> None:
    overrides, message = FAILED_CHECKS["not-on-main-diverged"]
    for file, content in overrides.items():
        gate.set(file, content)
    result = gate.start("workflow_run")
    assert (result.returncode, result.stdout, result.stderr) == (1, "", message + "\n")


def test_start_refuses_a_missing_credential_or_repository_before_any_call(gate: Gate) -> None:
    for env in (gate.env(token=None), gate.env(repository=None), gate.env(repository="a b")):
        result = gate.start("workflow_run", env=env)
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.count("\n") == 1
        assert GH_TOKEN not in result.stderr
    assert gate.calls() == []


@pytest.mark.parametrize("event", [None, "", "push", "workflow_dispatch\n", "WORKFLOW_RUN", "a b"])
def test_start_refuses_an_unknown_event(gate: Gate, event: str | None) -> None:
    result = gate.start(event)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert gate.calls() == []


@pytest.mark.parametrize(
    "bad",
    [
        TIP + "\n",
        "\n" + TIP,
        TIP.upper(),
        TIP[:39],
        TIP + "0",
        TIP[:7],
        "$(id)",
        TIP[:-1] + "٠",
        "main",
    ],
)
@pytest.mark.parametrize("event", ["workflow_run", "workflow_dispatch"])
def test_start_refuses_a_bad_sha_input_before_any_call(gate: Gate, bad: str, event: str) -> None:
    result = gate.start(event, bad)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == SHA_MESSAGE + "\n"
    assert gate.calls() == []


def test_start_treats_an_unset_input_as_the_tip(gate: Gate) -> None:
    _staged_nowhere(gate)
    result = gate.start("workflow_run", None)
    assert result.returncode == 0, result.stderr
    assert _keys(result.stdout)["explicit"] == "false"


@pytest.mark.parametrize("xtrace", [False, True])
def test_start_never_shows_credentials(gate: Gate, xtrace: bool) -> None:
    gate.set("head-python.txt", "HTTP/2 200\r\n\r\n")
    env = {
        **gate.env(extra={"REGISTRY_USER": "example-actor", "REGISTRY_TOKEN": LOGIN_TOKEN}),
        "EVENT_NAME": "workflow_dispatch",
        "INPUT_SHA": "",
    }
    result = gate.run_script(GATE, "start", env=env, xtrace=xtrace)
    assert result.returncode == 1
    for secret in (GH_TOKEN, LOGIN_TOKEN, REGISTRY_TOKEN):
        assert secret not in result.stdout + result.stderr


def test_start_does_not_depend_on_the_working_directory(gate: Gate, tmp_path: Path) -> None:
    _staged_nowhere(gate)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = {**gate.env(), "EVENT_NAME": "workflow_run", "INPUT_SHA": ""}
    result = subprocess.run(
        ["bash", str(GATE), "start"],
        env=env,
        cwd=elsewhere,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("proceed=true\nreason=rolling out " + SHA7 + "\n")


def test_start_ignores_a_stale_deploy_target_in_the_environment(gate: Gate) -> None:
    # The helper's environment says production; an unstaged tip would be refused there.
    _staged_nowhere(gate)
    result = gate.start("workflow_run")
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("proceed=true\nreason=rolling out " + SHA7 + "\n")
    # And a bad value in the environment changes nothing, since the gate sets the target itself.
    bad = {**gate.env(), "DEPLOY_TARGET": "bogus", "EVENT_NAME": "workflow_run", "INPUT_SHA": ""}
    result = gate.run_script(GATE, "start", env=bad)
    assert result.returncode == 0, result.stderr
    assert TARGET_MESSAGE not in result.stderr


class StubTree:
    """rollout_gate.sh in a `staging/` directory beside a stub `production/resolve_commit.sh`.

    The gate finds the resolver at ../production relative to itself, so the stub answers instead of
    the real one: its stdout is the `answer` file, its stderr the `reason` file, its exit code
    `status`.
    """

    def __init__(self, tmp_path: Path) -> None:
        """Lay out `<tmp>/tree/staging/` (the gate) and `<tmp>/tree/production/` (the stub)."""
        self.root = tmp_path / "tree"
        staging = self.root / "staging"
        production = self.root / "production"
        staging.mkdir(parents=True)
        production.mkdir()
        self.gate = staging / "rollout_gate.sh"
        shutil.copy(GATE, self.gate)
        resolver = production / "resolve_commit.sh"
        resolver.write_text(
            "#!/bin/bash\n"
            'here="$(dirname "$0")"\n'
            'cat "$here/answer"\n'
            'cat "$here/reason" >&2\n'
            'exit "$(cat "$here/status")"\n'
        )
        resolver.chmod(0o755)
        self.answer(valid_answer())
        self.refuse("", 0)

    def answer(self, text: str) -> None:
        """What the stub resolver prints on stdout."""
        (self.root / "production" / "answer").write_text(text)

    def refuse(self, stderr: str, status: int) -> None:
        """What the stub resolver prints on stderr, and its exit code."""
        (self.root / "production" / "reason").write_text(stderr)
        (self.root / "production" / "status").write_text(f"{status}\n")

    def start(
        self,
        event: str = "workflow_run",
        *,
        extra: dict[str, str] | None = None,
        cwd: Path | None = None,
        script: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run `rollout_gate.sh start` from the tree (the resolver needs no credentials here)."""
        env = {"PATH": "/usr/bin:/bin", "EVENT_NAME": event, "INPUT_SHA": "", **(extra or {})}
        return subprocess.run(
            ["bash", script or str(self.gate), "start"],
            env=env,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )


def valid_answer(
    *,
    sha: str = TIP,
    python_digest: str = DIGEST,
    frontend_digest: str = DIGEST,
    staged_on: str = "",
    explicit: str = "false",
) -> str:
    """The resolver's five lines."""
    return (
        f"sha={sha}\npython_digest={python_digest}\nfrontend_digest={frontend_digest}\n"
        f"staged_on={staged_on}\nexplicit={explicit}\n"
    )


@pytest.fixture
def tree(tmp_path: Path) -> StubTree:
    return StubTree(tmp_path)


def test_start_reads_only_the_first_line_of_the_resolver_stderr_on_not_ready(
    tree: StubTree,
) -> None:
    tree.refuse("line1\nreason=evil\n", 3)
    result = tree.start("workflow_run")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "proceed=false\nreason=nothing to roll out: line1\n"
    assert result.stderr == ""


def test_start_reports_only_the_first_line_of_the_resolver_stderr_on_a_refusal(
    tree: StubTree,
) -> None:
    tree.refuse("line1\nreason=evil\n", 1)
    result = tree.start("workflow_run")
    assert (result.returncode, result.stdout, result.stderr) == (1, "", "line1\n")


def test_start_passes_the_stub_resolvers_five_keys_through(tree: StubTree) -> None:
    tree.answer(valid_answer(staged_on=STAGED_ON, explicit="true"))
    result = tree.start("workflow_dispatch")
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        valid_answer(staged_on=STAGED_ON, explicit="true")
        + f"proceed=true\nreason=rolling out {SHA7}\n"
    )


UNEXPECTED_ANSWERS = {
    "extra-key": valid_answer() + "evil=1\n",
    "extra-key-first": "evil=1\n" + valid_answer(),
    "extra-key-between": valid_answer().replace("staged_on", "evil=1\nstaged_on"),
    "injected-proceed": valid_answer() + "proceed=true\n",
    "newline-in-staged-on": valid_answer(staged_on=STAGED_ON + "\nevil=1"),
    "newline-in-explicit": valid_answer(explicit="false\nproceed=true"),
    "repeated-key": valid_answer() + f"sha={TIP}\n",
    "missing-key": valid_answer().replace("explicit=false\n", ""),
    "reordered": "explicit=false\n" + valid_answer().replace("explicit=false\n", ""),
    "line-without-key": valid_answer() + "garbage\n",
    "bad-sha": valid_answer(sha=TIP.upper()),
    "short-sha": valid_answer(sha=TIP[:39]),
    "bad-python-digest": valid_answer(python_digest="sha256:" + "AB" * 32),
    "short-frontend-digest": valid_answer(frontend_digest="sha256:" + "ab" * 31),
    "digest-without-prefix": valid_answer(python_digest="ab" * 32),
    "bad-staged-on": valid_answer(staged_on="yesterday"),
    "staged-on-with-offset": valid_answer(staged_on="2026-11-13T08:30:00+00:00"),
    "bad-explicit": valid_answer(explicit="yes"),
    "empty-explicit": valid_answer(explicit=""),
    "empty-output": "",
}


@pytest.mark.parametrize("event", ["workflow_run", "workflow_dispatch"])
@pytest.mark.parametrize("name", UNEXPECTED_ANSWERS)
def test_start_emits_only_the_five_checked_keys_and_fails_closed_otherwise(
    tree: StubTree, name: str, event: str
) -> None:
    tree.answer(UNEXPECTED_ANSWERS[name])
    result = tree.start(event)
    assert result.returncode == 1, result.stdout
    assert result.stdout == ""
    assert result.stderr == "unexpected answer from the commit resolver\n"


def test_start_is_not_redirected_by_a_cdpath_in_the_environment(
    tree: StubTree, tmp_path: Path
) -> None:
    # A relative script path with a CDPATH that holds a decoy `tree/staging` would otherwise send
    # the gate's own `cd` to the decoy (and make cd print the directory).
    decoy = tmp_path / "decoy"
    (decoy / "tree" / "staging").mkdir(parents=True)
    result = tree.start(
        extra={"CDPATH": str(decoy)}, cwd=tree.root.parent, script="tree/staging/rollout_gate.sh"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == valid_answer() + f"proceed=true\nreason=rolling out {SHA7}\n"


# --- rollout_gate.sh current ------------------------------------------------------------------


def test_current_true_false_and_explicit(gate: Gate) -> None:
    # The tip is still the planned commit.
    result = gate.run_script(GATE, "current", TIP, "false")
    assert (result.returncode, result.stdout, result.stderr) == (0, "current=true\n", "")
    assert gate.gh_calls() == [f"api repos/{REPOSITORY}/commits/main --jq .sha"]
    # Main moved on.
    gate.set("tip.json", OLDER + "\n")
    result = gate.run_script(GATE, "current", TIP, "false")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "current=false\n"
    assert result.stderr == (
        f"superseded: main moved to {OLDER[:7]}; the run for the newer commit rolls it out\n"
    )
    # An explicit sha never asks.
    before = len(gate.gh_calls())
    result = gate.run_script(GATE, "current", TIP, "true")
    assert (result.returncode, result.stdout, result.stderr) == (0, "current=true\n", "")
    assert len(gate.gh_calls()) == before


def test_current_compares_the_whole_sha_not_a_seven_character_prefix(gate: Gate) -> None:
    other = TIP[:7] + OLDER[7:]
    assert other != TIP
    gate.set("tip.json", other + "\n")
    result = gate.run_script(GATE, "current", TIP, "false")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "current=false\n"
    assert result.stderr.startswith(f"superseded: main moved to {SHA7};")


def test_current_explicit_needs_no_credentials(gate: Gate) -> None:
    env = gate.env(token=None, repository=None)
    result = gate.run_script(GATE, "current", TIP, "true", env=env)
    assert (result.returncode, result.stdout) == (0, "current=true\n")
    assert gate.calls() == []


@pytest.mark.parametrize(
    "tip",
    ["", "\n", "not-a-sha\n", OLDER.upper() + "\n", OLDER[:39] + "\n", OLDER + "\n" + OLDER + "\n"],
)
def test_current_fails_on_an_unreadable_tip(gate: Gate, tip: str) -> None:
    gate.set("tip.json", tip)
    result = gate.run_script(GATE, "current", TIP, "false")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert "current=" not in result.stderr


def test_current_fails_when_gh_fails_without_showing_its_error(gate: Gate) -> None:
    gate.flag("fail-gh-tip")
    result = gate.run_script(GATE, "current", TIP, "false")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert GH_TOKEN not in result.stderr
    assert "stub failure" not in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        (),
        (TIP,),
        (TIP, "maybe"),
        (TIP, "True"),
        (TIP, ""),
        (TIP[:39], "false"),
        (TIP.upper(), "true"),
        (TIP + "\n", "false"),
        (TIP, "false", "extra"),
    ],
)
def test_current_refuses_a_bad_argument_without_a_call(gate: Gate, args: Sequence[str]) -> None:
    result = gate.run_script(GATE, "current", *args)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert gate.calls() == []


@pytest.mark.parametrize(
    "token,repository",
    [(None, REPOSITORY), ("", REPOSITORY), (GH_TOKEN, None), (GH_TOKEN, "bad repo")],
)
def test_current_refuses_a_missing_credential_or_repository(
    gate: Gate, token: str | None, repository: str | None
) -> None:
    result = gate.run_script(
        GATE, "current", TIP, "false", env=gate.env(token=token, repository=repository)
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert gate.calls() == []


@pytest.mark.parametrize("args", [(), ("stop",), ("START",)])
def test_the_gate_refuses_an_unknown_or_missing_command(gate: Gate, args: Sequence[str]) -> None:
    result = gate.run_script(GATE, *args)
    assert result.returncode == 1
    assert result.stdout == ""
    assert gate.calls() == []


# --- record_staged.sh -------------------------------------------------------------------------


class RecordingRunner:
    """A runner that answers `gh` like the API and keeps what it was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        env: object = None,
        input: str | None = None,
    ) -> sr.Completed:
        self.calls.append((list(argv), input))
        if argv[-2] == "--input" and argv[-3].endswith("/deployments"):
            return sr.Completed(0, json.dumps({"id": DEPLOYMENT_ID}), "")
        return sr.Completed(0, "{}", "")


def test_record_sends_the_same_bodies_as_the_rollout_core(gate: Gate) -> None:
    result = gate.run_script(RECORD, TIP)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"Recorded {SHA7} as staged\n"
    assert result.stderr == ""

    runner = RecordingRunner()
    target = dataclasses.replace(STAGING, repo=REPOSITORY)
    sr.record_staged(runner, target, TIP)
    core = [
        (argv[argv.index("POST") + 1], json.loads(stdin or "null")) for argv, stdin in runner.calls
    ]
    assert len(core) == 2
    assert gate.posts() == core
    assert [p for p, _ in gate.posts()] == [
        f"repos/{REPOSITORY}/deployments",
        f"repos/{REPOSITORY}/deployments/{DEPLOYMENT_ID}/statuses",
    ]
    # The core's bodies, spelled out, so a change on either side shows here.
    assert gate.posts()[0][1] == {
        "ref": TIP,
        "environment": "staging",
        "auto_merge": False,
        "required_contexts": [],
        "transient_environment": False,
        "production_environment": False,
        "description": "staging rollout",
    }
    assert gate.posts()[1][1] == {
        "state": "success",
        "environment": "staging",
        "auto_inactive": False,
        "description": "staging rollout finished",
    }
    assert gate.gh_calls() == [
        f"api --method POST repos/{REPOSITORY}/deployments --input -",
        f"api --method POST repos/{REPOSITORY}/deployments/{DEPLOYMENT_ID}/statuses --input -",
    ]


@pytest.mark.parametrize(
    "answer",
    [
        "",
        "not json",
        "[]",
        "null",
        "{}",
        '{"id": null}',
        '{"id": "4242"}',
        '{"id": "abc"}',
        '{"id": -1}',
        '{"id": 1.5}',
        '{"id": true}',
        '{"id": [4242]}',
        '{"id": "42; id"}',
        '{"id": 1e30}',
    ],
)
def test_record_refuses_a_malformed_id_and_reports_the_retry_line(gate: Gate, answer: str) -> None:
    gate.set("post-deployment.json", answer)
    result = gate.run_script(RECORD, TIP)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == RETRY_LINE + "\n"
    # Only the deployment was created; no status was posted for a doubtful id.
    assert len(gate.posts()) == 1


@pytest.mark.parametrize("key", ["post-deployment", "post-status"])
def test_record_reports_the_retry_line_when_a_call_fails_and_shows_no_secret(
    gate: Gate, key: str
) -> None:
    gate.flag(f"fail-gh-{key}")
    result = gate.run_script(RECORD, TIP)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == RETRY_LINE + "\n"
    assert GH_TOKEN not in result.stdout + result.stderr


@pytest.mark.parametrize("key", ["post-deployment", "post-status"])
@pytest.mark.parametrize("code", ["403", "404"])
@pytest.mark.parametrize("xtrace", [False, True])
def test_record_names_the_http_code_gh_answered_before_the_retry_line(
    gate: Gate, key: str, code: str, xtrace: bool
) -> None:
    (gate.dir / f"fail-gh-{key}").write_text(f" (HTTP {code}) forbidden, token {GH_TOKEN}")
    result = gate.run_script(RECORD, TIP, xtrace=xtrace)
    assert result.returncode == 1
    assert result.stdout == ""
    if xtrace:
        # The trace lines are interleaved; the two messages are still there, in this order.
        lines = result.stderr.splitlines()
        assert lines.index(f"gh answered HTTP {code}") < lines.index(RETRY_LINE)
    else:
        assert result.stderr == f"gh answered HTTP {code}\n{RETRY_LINE}\n"
    assert GH_TOKEN not in result.stdout + result.stderr
    assert "forbidden" not in result.stdout + result.stderr


@pytest.mark.parametrize("key", ["post-deployment", "post-status"])
@pytest.mark.parametrize(
    "text", [" no code here", " (HTTP 40) short", " (HTTP 4031) long", " http 403"]
)
def test_record_shows_no_http_line_without_a_three_digit_code(
    gate: Gate, key: str, text: str
) -> None:
    (gate.dir / f"fail-gh-{key}").write_text(text)
    result = gate.run_script(RECORD, TIP)
    assert result.returncode == 1
    assert result.stderr == RETRY_LINE + "\n"


def test_record_with_a_failed_status_has_created_the_deployment_first(gate: Gate) -> None:
    gate.flag("fail-gh-post-status")
    gate.run_script(RECORD, TIP)
    assert [path for path, _ in gate.posts()] == [
        f"repos/{REPOSITORY}/deployments",
        f"repos/{REPOSITORY}/deployments/{DEPLOYMENT_ID}/statuses",
    ]


@pytest.mark.parametrize("xtrace", [False, True])
@pytest.mark.parametrize("failing", [False, True])
def test_record_never_prints_the_token(gate: Gate, xtrace: bool, failing: bool) -> None:
    if failing:
        gate.flag("fail-gh-post-status")
    result = gate.run_script(RECORD, TIP, xtrace=xtrace)
    assert GH_TOKEN not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "args",
    [(), ("",), (TIP.upper(),), (TIP[:39],), (TIP + "0",), (TIP + "\n",), ("$(id)",), (TIP, TIP)],
)
def test_record_refuses_a_bad_sha_without_a_call(gate: Gate, args: Sequence[str]) -> None:
    result = gate.run_script(RECORD, *args)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert gate.calls() == []


@pytest.mark.parametrize(
    "token,repository", [(None, REPOSITORY), ("", REPOSITORY), (GH_TOKEN, None), (GH_TOKEN, "x")]
)
def test_record_refuses_a_missing_credential_or_repository(
    gate: Gate, token: str | None, repository: str | None
) -> None:
    result = gate.run_script(RECORD, TIP, env=gate.env(token=token, repository=repository))
    assert result.returncode == 1
    assert result.stdout == ""
    assert gate.calls() == []


# --- both scripts -----------------------------------------------------------------------------


@pytest.mark.parametrize("script", [GATE, RECORD], ids=lambda path: path.name)
def test_scripts_are_bash_strict_quoted_and_executable(script: Path) -> None:
    text = script.read_text()
    lines = text.splitlines()
    code = "\n".join(line for line in lines if not line.lstrip().startswith("#"))

    assert lines[0] == "#!/bin/bash"
    strict = lines.index("set -euo pipefail")
    locale = lines.index("export LC_ALL=C")
    assert strict < locale < next(i for i, line in enumerate(lines) if "=~" in line)
    assert not re.search(r"\beval\b|\b(?:ba)?sh -c\b|\bset -x\b|\bsource\b", code)
    if script == RECORD:
        # jq as a command (not just inside a gh --jq argument): the record builds and reads JSON.
        assert re.search(r"(?:^|[\s(|;])jq\s", code, re.MULTILINE)
    else:
        # The gate parses no JSON of its own: only gh's `--jq .sha`, and the resolver's key=value
        # lines.
        assert code.count("--jq .sha") == 1
        assert "jq" not in code.replace("--jq .sha", "")
    assert "${{" not in text
    assert _unquoted_expansions(text) == []
    assert script.stat().st_mode & stat.S_IXUSR
    assert os.access(script, os.X_OK)
    subprocess.run(["bash", "-n", str(script)], check=True)
    tracked = subprocess.run(
        ["git", "ls-files", "-s", str(script)], capture_output=True, text=True, cwd=ROOT, check=True
    ).stdout
    assert tracked == "" or tracked.startswith("100755")


@pytest.mark.parametrize("script", [GATE, RECORD], ids=lambda path: path.name)
def test_scripts_hold_no_account_node_region_or_address(script: Path) -> None:
    text = script.read_text()
    assert not re.search(
        r"arn:aws|\b\d{12}\b|mi-[0-9a-f]{17}|amsyrzmi|\b\d{1,3}(\.\d{1,3}){3}\b", text
    )


def test_the_gate_runs_the_production_resolver_from_its_sibling_directory() -> None:
    code = GATE.read_text()
    assert "../production" in code
    assert "DEPLOY_TARGET=staging" in code
