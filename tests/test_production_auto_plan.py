"""deploy/production/auto_plan.sh: the decisions of the deploy-production-auto workflow.

The script is copied next to a stub ``resolve_commit.sh`` (its exit code, stdout and stderr are set
per test, so the resolver itself is not tested here) and run against the stateful fake ``gh`` of
``tests/fake_gh.py``. It is never run against GitHub. Stand-ins only: the repository
``example-owner/CSAV2`` and a synthetic sha.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.fake_gh import BOT, MISSING_FIELD, REPO, TIP, Gh
from tests.test_production_deploy_assets import _unquoted_expansions

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "production" / "auto_plan.sh"

SHA7 = TIP[:7]
OTHER_SHA = "f" * 40
DIGEST = "sha256:" + "a" * 64
STAGED_ON = "2026-11-18T08:00:00Z"
TOKEN = "test-token-marker-9d2c"
RUNS = f"repos/{REPO}/actions/workflows/deploy-production.yml/runs"
NOT_READY_CI = f"the ci run for {SHA7} has not succeeded on main"
NOT_READY_IMAGES = f"images for {SHA7} are not published (csa-frontend)"
UNEXPECTED = "unexpected answer from the commit resolver"

STUB_RESOLVER = r"""#!/bin/bash
# A stand-in for resolve_commit.sh: records how it was called and answers as the test says.
{
    printf 'target=%s\n' "${DEPLOY_TARGET-unset}"
    printf 'argc=%s\n' "$#"
    printf 'arg1=%s\n' "${1-unset}"
    printf 'token=%s\n' "${GH_TOKEN+set}"
    printf 'repository=%s\n' "${REPOSITORY-unset}"
    printf 'namespace=%s\n' "${IMAGE_NAMESPACE-unset}"
    printf 'registry_user=%s\n' "${REGISTRY_USER-unset}"
    printf 'registry_token=%s\n' "${REGISTRY_TOKEN+set}"
} >> "$STUB_LOG"
printf '%b' "$STUB_OUT"
printf '%b' "$STUB_ERR" >&2
exit "${STUB_STATUS:-0}"
"""

# A gh that ignores the query and prints a fixed answer, for answers the fake never gives.
FIXED_GH = r"""#!/bin/bash
printf '%b' "$FIXED_GH_OUT"
exit "${FIXED_GH_STATUS:-0}"
"""


def resolver_answer(
    sha: str = TIP,
    staged_on: str = STAGED_ON,
    *,
    python_digest: str = DIGEST,
    frontend_digest: str = DIGEST,
    explicit: str = "false",
) -> str:
    return (
        f"sha={sha}\npython_digest={python_digest}\nfrontend_digest={frontend_digest}\n"
        f"staged_on={staged_on}\nexplicit={explicit}\n"
    )


@dataclass
class Harness:
    gh: Gh
    tree: Path

    @property
    def script(self) -> Path:
        return self.tree / "deploy" / "production" / "auto_plan.sh"

    @property
    def stub_log(self) -> Path:
        return self.tree / "stub.log"

    def check(
        self,
        *,
        status: int = 0,
        out: str | None = None,
        err: str = "",
        env: dict[str, str] | None = None,
        args: tuple[str, ...] = ("check",),
    ) -> subprocess.CompletedProcess[str]:
        values = {
            "GH_TOKEN": TOKEN,
            "REPOSITORY": REPO,
            "IMAGE_NAMESPACE": "ghcr.io/example-owner",
            "STUB_LOG": str(self.stub_log),
            "STUB_STATUS": str(status),
            "STUB_OUT": resolver_answer() if out is None else out,
            "STUB_ERR": err,
        }
        values.update(env or {})
        return self.gh.run(self.script, *args, env=values)

    def stub_calls(self) -> list[str]:
        return self.stub_log.read_text().splitlines() if self.stub_log.exists() else []

    def outputs(self, result: subprocess.CompletedProcess[str]) -> dict[str, str]:
        assert result.returncode == 0, result.stderr
        found = dict(line.split("=", 1) for line in result.stdout.splitlines())
        assert result.stdout.count("\n") == len(found)
        return found


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    gh = Gh(tmp_path)
    tree = tmp_path / "tree"
    folder = tree / "deploy" / "production"
    folder.mkdir(parents=True)
    shutil.copy(SCRIPT, folder / "auto_plan.sh")
    stub = folder / "resolve_commit.sh"
    stub.write_text(STUB_RESOLVER)
    stub.chmod(0o755)
    return Harness(gh, tree)


def reads(harness: Harness) -> list[str]:
    return harness.gh.commands()


# --- the outcomes ----------------------------------------------------------------------------


def test_check_proceeds_for_a_ready_staged_tip_with_no_run(harness: Harness) -> None:
    # Another sha's run and a failed run of the tip do not matter; the ambient DEPLOY_TARGET is
    # ignored; the resolver is asked for the tip (an empty sha) in its staging mode and gets the
    # whole environment it needs.
    harness.gh.add_run(status="waiting", conclusion=None, head_sha=OTHER_SHA)
    harness.gh.add_run(status="completed", conclusion="failure", head_sha=TIP)
    harness.gh.reset_log()
    result = harness.check(
        env={
            "DEPLOY_TARGET": "production",
            "REGISTRY_USER": "someone",
            "REGISTRY_TOKEN": "registry-marker",
        }
    )

    assert harness.outputs(result) == {
        "sha": TIP,
        "proceed": "true",
        "reason": f"planning {SHA7}",
    }
    assert result.stdout.splitlines() == [
        f"sha={TIP}",
        "proceed=true",
        f"reason=planning {SHA7}",
    ]
    assert result.stderr == ""
    assert harness.gh.writes() == []
    assert reads(harness) == [f"api GET {RUNS}?head_sha={TIP}&per_page=100"]
    assert harness.stub_calls() == [
        "target=staging",
        "argc=1",
        "arg1=",
        "token=set",
        f"repository={REPO}",
        "namespace=ghcr.io/example-owner",
        "registry_user=someone",
        "registry_token=set",
    ]
    assert TOKEN not in result.stdout + result.stderr


@pytest.mark.parametrize("message", [NOT_READY_CI, NOT_READY_IMAGES])
def test_check_ends_green_when_the_tip_is_not_ready(harness: Harness, message: str) -> None:
    result = harness.check(status=3, out="", err=message + "\n")

    assert result.stdout == f"proceed=false\nreason=nothing to plan: {message}\n"
    assert (result.returncode, result.stderr) == (0, "")
    assert reads(harness) == []


@pytest.mark.parametrize("app", ["python", "frontend"])
def test_check_knows_the_images_line_of_either_app(harness: Harness, app: str) -> None:
    message = f"images for {SHA7} are not published (csa-{app})"
    result = harness.check(status=3, out="", err=message + "\n")
    assert harness.outputs(result)["reason"] == f"nothing to plan: {message}"


def test_check_ends_green_when_the_tip_is_not_staged(harness: Harness) -> None:
    # Staging paused, superseded, or the record job failed: the resolver in staging mode answers
    # with an empty staged_on. A run of the tip would not matter either: this is decided first.
    harness.gh.add_run(status="waiting", conclusion=None, head_sha=TIP)
    harness.gh.reset_log()
    result = harness.check(out=resolver_answer(staged_on=""))

    assert result.stdout.splitlines() == [
        f"sha={TIP}",
        "proceed=false",
        f"reason=nothing to plan: main's tip {SHA7} is not staged yet",
    ]
    assert (result.returncode, result.stderr) == (0, "")
    assert reads(harness) == []


@pytest.mark.parametrize(
    ("status", "conclusion"),
    [
        ("queued", None),
        ("requested", None),
        ("in_progress", None),
        ("waiting", None),
        ("pending", None),
        ("completed", "success"),
    ],
)
def test_check_skips_when_a_run_for_the_tip_is_waiting_queued_in_progress_or_succeeded(
    harness: Harness, status: str, conclusion: str | None
) -> None:
    # A newer failed run of the same commit does not hide the blocking one.
    run_id = harness.gh.add_run(status=status, conclusion=conclusion, head_sha=TIP)
    harness.gh.add_run(status="completed", conclusion="failure", head_sha=TIP)
    result = harness.check()

    assert result.stdout.splitlines() == [
        f"sha={TIP}",
        "proceed=false",
        f"reason=a deploy-production run for {SHA7} already exists: {run_id}",
    ]
    assert (result.returncode, result.stderr) == (0, "")
    assert harness.gh.writes() == []


def test_check_names_the_newest_blocking_run(harness: Harness) -> None:
    harness.gh.add_run(status="completed", conclusion="success", head_sha=TIP)
    newest = harness.gh.add_run(status="waiting", conclusion=None, head_sha=TIP)
    result = harness.check()
    assert harness.outputs(result)["reason"].endswith(f"already exists: {newest}")


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "timed_out", "neutral"])
def test_check_does_not_skip_for_a_failed_or_cancelled_run_of_the_tip(
    harness: Harness, conclusion: str
) -> None:
    # The early manual dispatch that failed with "not staged yet" must not stop the auto plan.
    harness.gh.add_run(status="completed", conclusion=conclusion, head_sha=TIP)
    result = harness.check()
    assert harness.outputs(result)["proceed"] == "true"


def test_check_does_not_skip_for_a_run_of_another_sha(harness: Harness) -> None:
    for status, conclusion in (("waiting", None), ("completed", "success"), ("queued", None)):
        harness.gh.add_run(status=status, conclusion=conclusion, head_sha=OTHER_SHA)
    result = harness.check()
    assert harness.outputs(result) == {"sha": TIP, "proceed": "true", "reason": f"planning {SHA7}"}


def test_check_counts_only_runs_of_the_tip_even_if_the_listing_holds_others(
    harness: Harness, tmp_path: Path
) -> None:
    # The query filters by head_sha, but the script does not rely on that alone.
    fixed = tmp_path / "fixed" / "gh"
    fixed.parent.mkdir()
    fixed.write_text(FIXED_GH)
    fixed.chmod(0o755)
    listing = {
        "total_count": 2,
        "workflow_runs": [
            {"id": 11, "head_sha": OTHER_SHA, "status": "waiting", "conclusion": None},
            {"id": 12, "head_sha": TIP, "status": "completed", "conclusion": "failure"},
        ],
    }
    path = f"{fixed.parent}:{harness.gh.environment()['PATH']}"
    env = {"PATH": path, "FIXED_GH_OUT": json.dumps(listing)}
    assert harness.outputs(harness.check(env=env))["proceed"] == "true"
    listing["workflow_runs"].append(
        {"id": 13, "head_sha": TIP, "status": "waiting", "conclusion": None}
    )
    env["FIXED_GH_OUT"] = json.dumps(listing)
    assert harness.outputs(harness.check(env=env))["reason"].endswith("already exists: 13")


# --- failures --------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [1, 2, 4, 127])
def test_check_fails_closed_on_any_other_resolver_failure_with_nothing_on_stdout(
    harness: Harness, status: int
) -> None:
    result = harness.check(
        status=status, out="sha=" + TIP + "\n", err="commit 0123456 is not on main\n"
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "commit 0123456 is not on main\n"
    assert reads(harness) == []


def test_check_fails_with_a_fixed_line_when_the_resolver_says_nothing(harness: Harness) -> None:
    result = harness.check(status=1, out="", err="")
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "the commit could not be resolved\n"


@pytest.mark.parametrize(
    "answer",
    [
        resolver_answer() + "extra=1\n",
        resolver_answer() + f"sha={TIP}\n",
        resolver_answer().replace("sha=", "tip=", 1),
        resolver_answer(python_digest="sha256:" + "A" * 64),
        resolver_answer(frontend_digest="sha256:abc"),
        resolver_answer(sha=TIP[:39]),
        resolver_answer(sha=TIP.upper()),
        resolver_answer(sha=TIP + "\r"),
        resolver_answer(staged_on="yesterday"),
        resolver_answer(staged_on=STAGED_ON + "\r"),
        resolver_answer(explicit="maybe"),
        resolver_answer().replace("explicit=false\n", ""),
        "".join(sorted(resolver_answer().splitlines(keepends=True))),
        resolver_answer() + "not a key value line\n",
        "",
    ],
    ids=[
        "extra key",
        "repeated key",
        "renamed key",
        "uppercase digest",
        "short digest",
        "short sha",
        "uppercase sha",
        "carriage return in the sha",
        "bad date",
        "carriage return in the date",
        "bad explicit",
        "missing key",
        "keys out of order",
        "line without an equals sign",
        "empty answer",
    ],
)
def test_check_refuses_a_malformed_resolver_answer(harness: Harness, answer: str) -> None:
    result = harness.check(out=answer)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == UNEXPECTED + "\n"
    assert reads(harness) == []


def test_check_fails_when_the_run_list_cannot_be_read(harness: Harness) -> None:
    harness.gh.set_state(logged_out=True)
    result = harness.check()
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "could not read the deploy-production runs from GitHub\n"
    assert "gh auth login" not in result.stderr


@pytest.mark.parametrize(
    "listing",
    [
        "",
        "  \n",
        "not json",
        "{}",
        "[]",
        '{"workflow_runs": {}}',
        '{"workflow_runs": null}',
        '{"workflow_runs": ["x"]}',
        json.dumps(
            {
                "workflow_runs": [
                    {"id": None, "head_sha": TIP, "status": "waiting", "conclusion": None}
                ]
            }
        ),
        json.dumps(
            {
                "workflow_runs": [
                    {"id": "12a", "head_sha": TIP, "status": "queued", "conclusion": None}
                ]
            }
        ),
    ],
)
def test_check_refuses_a_run_list_it_cannot_read(
    harness: Harness, tmp_path: Path, listing: str
) -> None:
    fixed = tmp_path / "fixed" / "gh"
    fixed.parent.mkdir()
    fixed.write_text(FIXED_GH)
    fixed.chmod(0o755)
    path = f"{fixed.parent}:{harness.gh.environment()['PATH']}"
    result = harness.check(env={"PATH": path, "FIXED_GH_OUT": listing})
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "unexpected answer for the deploy-production runs\n"


def test_check_fails_when_the_run_list_call_fails(harness: Harness, tmp_path: Path) -> None:
    fixed = tmp_path / "fixed" / "gh"
    fixed.parent.mkdir()
    fixed.write_text(FIXED_GH)
    fixed.chmod(0o755)
    path = f"{fixed.parent}:{harness.gh.environment()['PATH']}"
    result = harness.check(
        env={"PATH": path, "FIXED_GH_OUT": '{"workflow_runs": []}', "FIXED_GH_STATUS": "1"}
    )
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "could not read the deploy-production runs from GitHub\n"


# --- what reaches the output -----------------------------------------------------------------


@pytest.mark.parametrize(
    "stderr",
    [
        "::set-output name=proceed::true\n",
        "ghp_" + "x" * 36 + "\n",
        "reason=planning evil\nproceed=true\n",
        "the ci run for ABCDEF0 has not succeeded on main\n",
        f"the ci run for {SHA7} has not succeeded on main \n",
        f"the ci run for {SHA7}0 has not succeeded on main\n",
        f"images for {SHA7}0 are not published (csa-python)\n",
        f"images for {SHA7} are not published (csa-evil)\n",
        f"images for {SHA7} are not published (csa-python) and more\n",
        f"images for {SHA7} are not published (csa-python)\r\n",
        "",
    ],
)
def test_the_reason_holds_only_fixed_text_and_sha7(harness: Harness, stderr: str) -> None:
    result = harness.check(status=3, out="", err=stderr)

    assert result.stdout == ("proceed=false\nreason=nothing to plan: the commit is not ready yet\n")
    assert (result.returncode, result.stderr) == (0, "")


def test_only_the_first_resolver_line_is_ever_considered(harness: Harness) -> None:
    result = harness.check(
        status=3, out="", err=NOT_READY_CI + "\nreason=planning evil\n::error::boom\n"
    )
    assert result.stdout == f"proceed=false\nreason=nothing to plan: {NOT_READY_CI}\n"


def test_every_stdout_line_is_a_known_key_and_no_secret_is_printed(harness: Harness) -> None:
    runs = [(status, None) for status in ("queued", "waiting")] + [("completed", "success")]
    cases = []
    for status, conclusion in runs:
        harness.gh.add_run(status=status, conclusion=conclusion, head_sha=TIP)
        cases.append(harness.check())
    cases.append(harness.check(out=resolver_answer(staged_on="")))
    cases.append(harness.check(status=3, out="", err=NOT_READY_CI + "\n"))
    cases.append(harness.check(status=1, err="boom\n"))
    for result in cases:
        for line in result.stdout.splitlines():
            assert re.fullmatch(r"(sha|proceed|reason)=[^\n]*", line), line
        assert TOKEN not in result.stdout + result.stderr


# --- arguments and environment ---------------------------------------------------------------


@pytest.mark.parametrize("repository", ["", "a b", "a/b/c", "owner", "a/", "own\ner/name", "a/b;c"])
def test_the_repository_is_validated_before_anything_runs(
    harness: Harness, repository: str
) -> None:
    result = harness.check(env={"REPOSITORY": repository})
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "REPOSITORY must be set to owner/name\n"
    assert reads(harness) == [] and harness.stub_calls() == []


def test_the_token_must_be_set(harness: Harness) -> None:
    environment = harness.gh.environment(
        {
            "REPOSITORY": REPO,
            "STUB_LOG": str(harness.stub_log),
            "STUB_OUT": resolver_answer(),
        }
    )
    environment.pop("GH_TOKEN", None)
    result = subprocess.run(
        [str(harness.script), "check"],
        capture_output=True,
        text=True,
        env=environment,
        cwd=harness.tree,
        input="",
        check=False,
    )
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "GH_TOKEN must be set\n"
    assert reads(harness) == [] and harness.stub_calls() == []


def test_an_empty_token_is_not_a_token(harness: Harness) -> None:
    result = harness.check(env={"GH_TOKEN": ""})
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "GH_TOKEN must be set\n"
    assert reads(harness) == [] and harness.stub_calls() == []


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("extra",),
        ("check", "extra"),
        ("start", "extra"),
        ("supersede",),
        ("supersede", TIP, "x"),
    ],
)
def test_arguments_are_validated(harness: Harness, args: tuple[str, ...]) -> None:
    result = harness.check(args=args)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr.startswith("usage: auto_plan.sh")
    assert reads(harness) == [] and harness.stub_calls() == []


# --- a resolver line that is not plain text is never echoed ----------------------------------


@pytest.mark.parametrize(
    "stderr",
    [
        "::error::boom\n",
        "::set-output name=x::y\n",
        "boom\x1b[31m red\n",
        "tab\there\n",
        "carriage\rreturn\n",
        "é not ascii\n",
    ],
)
def test_check_never_echoes_a_resolver_line_that_is_not_plain_text(
    harness: Harness, stderr: str
) -> None:
    result = harness.check(status=1, err=stderr)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "the commit could not be resolved\n"


# --- supersede and start ---------------------------------------------------------------------

BOT_LOGIN = BOT["login"]
PLAN_APPLY_WAITING = [("plan", "completed", "success"), ("apply", "waiting", None)]
LISTING = f"api GET {RUNS}?status=waiting&per_page=100"
START_LIST = f"api GET {RUNS}?event=workflow_dispatch&branch=main&per_page=30"
DISPATCH = ["workflow", "run", "deploy-production.yml", "--repo", REPO, "--ref", "main"]

# A gh in front of the fake that, on the nth call whose argv matches a regex, patches the state
# (a person approving a run, a run finishing), fails, or prints a fixed answer, then hands over.
HOOK_GH = r"""#!__PYTHON__
import json, os, re, subprocess, sys
config = json.load(open(os.environ["HOOK_CONFIG"]))
counts_path = os.environ["HOOK_CONFIG"] + ".counts"
counts = json.load(open(counts_path)) if os.path.exists(counts_path) else {}
line = " ".join(sys.argv[1:])
for index, rule in enumerate(config):
    if not re.search(rule["match"], line):
        continue
    counts[str(index)] = counts.get(str(index), 0) + 1
    json.dump(counts, open(counts_path, "w"))
    if counts[str(index)] != rule["nth"]:
        continue
    if rule["action"] == "patch":
        state = json.load(open(os.environ["FAKE_GH_STATE"]))
        run = next(r for r in state["runs"] if r["id"] == rule["run"])
        for key in ("status", "conclusion"):
            if key in rule["set"]:
                run[key] = rule["set"][key]
        for job in run["jobs"]:
            if job["name"] in rule.get("jobs", {}):
                job["status"], job["conclusion"] = rule["jobs"][job["name"]]
        json.dump(state, open(os.environ["FAKE_GH_STATE"], "w"))
    elif rule["action"] == "fail":
        sys.stderr.write("gh: boom (HTTP 500)\n")
        sys.exit(1)
    elif rule["action"] == "print":
        sys.stdout.write(rule["stdout"])
        sys.exit(0)
os.execv(os.environ["HOOK_REAL_GH"], [os.environ["HOOK_REAL_GH"], *sys.argv[1:]])
"""


def stale_run(harness: Harness, **overrides: object) -> int:
    """A run that `supersede` may cancel: waiting, a bot dispatch on main, an older commit."""
    plan: dict[str, object] = {
        "status": "waiting",
        "conclusion": None,
        "event": "workflow_dispatch",
        "head_sha": OTHER_SHA,
        "head_branch": "main",
        "actor": BOT_LOGIN,
        "jobs": PLAN_APPLY_WAITING,
    }
    plan.update(overrides)
    return harness.gh.add_run(**plan)  # type: ignore[arg-type]


def supersede(
    harness: Harness, *, sha: str = TIP, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    values = {"GH_TOKEN": TOKEN, "REPOSITORY": REPO}
    values.update(env or {})
    return harness.gh.run(harness.script, "supersede", sha, env=values)


def start(
    harness: Harness, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    values = {"GH_TOKEN": TOKEN, "REPOSITORY": REPO, "START_POLL_SECONDS": "0"}
    values.update(env or {})
    return harness.gh.run(harness.script, "start", env=values)


def hook(harness: Harness, *rules: Mapping[str, object]) -> dict[str, str]:
    """The environment that puts the hook gh in front of the fake, with these rules."""
    folder = harness.tree / "hook-bin"
    folder.mkdir(exist_ok=True)
    wrapper = folder / "gh"
    wrapper.write_text(HOOK_GH.replace("__PYTHON__", sys.executable))
    wrapper.chmod(0o755)
    config = harness.tree / "hook.json"
    config.write_text(json.dumps(list(rules)))
    return {
        "PATH": f"{folder}:{harness.gh.environment()['PATH']}",
        "HOOK_CONFIG": str(config),
        "HOOK_REAL_GH": str(harness.gh.bin / "gh"),
    }


def cancels(harness: Harness) -> list[str]:
    return [c for c in harness.gh.commands() if c.startswith("api POST")]


def run_reads(harness: Harness, run_id: int) -> int:
    return harness.gh.commands().count(f"api GET repos/{REPO}/actions/runs/{run_id}")


def status_of(harness: Harness, run_id: int) -> tuple[str, str | None]:
    run = next(r for r in harness.gh.state()["runs"] if r["id"] == run_id)
    return run["status"], run["conclusion"]


def test_supersede_cancels_a_stale_waiting_auto_run(harness: Harness) -> None:
    first = stale_run(harness)
    second = stale_run(harness, head_sha="e" * 40)
    harness.gh.reset_log()
    result = supersede(harness)

    assert result.stdout == (
        f"cancelled run {second} (planned eeeeeee)\ncancelled run {first} (planned fffffff)\n"
    )
    assert (result.returncode, result.stderr) == (0, "")
    assert status_of(harness, first) == ("completed", "cancelled")
    assert status_of(harness, second) == ("completed", "cancelled")
    # Per run: its jobs, a fresh read of the run and its jobs, then exactly one cancel.
    runs = f"api GET repos/{REPO}/actions/runs"
    assert harness.gh.commands() == [
        LISTING,
        *[
            line
            for run_id in (second, first)
            for line in (
                f"{runs}/{run_id}/jobs",
                f"{runs}/{run_id}",
                f"{runs}/{run_id}/jobs",
                f"api POST repos/{REPO}/actions/runs/{run_id}/cancel",
            )
        ],
    ]
    assert TOKEN not in result.stdout + result.stderr


def test_supersede_with_nothing_waiting_prints_nothing_and_writes_nothing(
    harness: Harness,
) -> None:
    harness.gh.add_run()
    harness.gh.add_run(status="in_progress", conclusion=None, actor=BOT_LOGIN)
    harness.gh.reset_log()
    result = supersede(harness)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert harness.gh.commands() == [LISTING]


def test_supersede_leaves_a_manual_run_alone_even_when_stale_and_waiting(
    harness: Harness,
) -> None:
    manual = stale_run(harness, actor="reviewer-a")
    lookalike = stale_run(harness, actor="github-actions")
    rerun = stale_run(harness, actor=BOT_LOGIN, triggering_actor="reviewer-b")
    harness.gh.reset_log()
    result = supersede(harness)

    assert result.stdout.splitlines() == [
        f"left alone: run {rerun} (started by reviewer-b)",
        f"left alone: run {lookalike} (started by github-actions)",
        f"left alone: run {manual} (started by reviewer-a)",
    ]
    assert (result.returncode, result.stderr) == (0, "")
    assert harness.gh.writes() == []
    assert all(status_of(harness, r) == ("waiting", None) for r in (manual, lookalike, rerun))


def test_supersede_leaves_a_rollback_run_alone(harness: Harness) -> None:
    # A person's dispatch with an explicit sha is a person's run, whatever its commit.
    rollback = stale_run(harness, actor="reviewer-a", head_sha="d" * 40)
    result = supersede(harness)
    assert result.stdout == f"left alone: run {rollback} (started by reviewer-a)\n"
    assert harness.gh.writes() == []


@pytest.mark.parametrize(
    "jobs",
    [
        [("plan", "completed", "success"), ("apply", "in_progress", None)],
        [("plan", "completed", "success"), ("apply", "queued", None)],
        [("plan", "completed", "success"), ("apply", "pending", None)],
        [("plan", "completed", "success"), ("apply", "completed", "success")],
        [("plan", "in_progress", None), ("apply", "waiting", None)],
        [("plan", "completed", "failure"), ("apply", "waiting", None)],
        [
            ("plan", "completed", "success"),
            ("apply", "waiting", None),
            ("smoke", "in_progress", None),
        ],
        [("plan", "completed", "success"), ("apply", "waiting", None), ("smoke", "queued", None)],
        [("plan", "completed", "success")],
        [("apply", "waiting", None)],
        [
            ("plan", "completed", "success"),
            ("plan", "completed", "success"),
            ("apply", "waiting", None),
        ],
        [("plan", "completed", "success"), ("deploy", "waiting", None)],
        [],
    ],
    ids=[
        "apply in progress",
        "apply queued",
        "apply pending",
        "apply completed",
        "plan still running",
        "plan failed",
        "another job running",
        "another job queued",
        "only the plan job",
        "only the apply job",
        "plan twice",
        "apply under another name",
        "no jobs",
    ],
)
def test_supersede_never_cancels_a_run_whose_apply_is_not_waiting(
    harness: Harness, jobs: list[tuple[str, str, str | None]]
) -> None:
    run_id = stale_run(harness, jobs=jobs)
    result = supersede(harness)
    assert result.stdout == f"left alone: run {run_id} (apply is not waiting)\n"
    assert (result.returncode, result.stderr) == (0, "")
    assert harness.gh.writes() == []


def test_supersede_never_lists_a_run_that_is_in_progress(harness: Harness) -> None:
    # An approved run reads in_progress, not waiting: the listing (status=waiting) never has it.
    running = stale_run(
        harness,
        status="in_progress",
        conclusion=None,
        jobs=[("plan", "completed", "success"), ("apply", "in_progress", None)],
    )
    result = supersede(harness)
    assert (result.returncode, result.stdout) == (0, "")
    assert harness.gh.writes() == []
    assert status_of(harness, running) == ("in_progress", None)


def test_supersede_leaves_a_run_that_is_listed_but_not_waiting_alone(
    harness: Harness, tmp_path: Path
) -> None:
    # The listing is a claim of the API: the script checks the status itself.
    running = stale_run(harness, status="in_progress", conclusion=None)
    fixed = tmp_path / "fixed" / "gh"
    fixed.parent.mkdir()
    fixed.write_text(FIXED_GH)
    fixed.chmod(0o755)
    listing = {
        "workflow_runs": [
            {
                "id": running,
                "status": "in_progress",
                "event": "workflow_dispatch",
                "head_branch": "main",
                "head_sha": OTHER_SHA,
                "triggering_actor": {"login": BOT_LOGIN},
            }
        ]
    }
    path = f"{fixed.parent}:{harness.gh.environment()['PATH']}"
    result = supersede(harness, env={"PATH": path, "FIXED_GH_OUT": json.dumps(listing)})
    assert result.stdout == f"left alone: run {running} (apply is not waiting)\n"
    assert harness.gh.writes() == []


def test_supersede_leaves_a_run_for_the_same_commit_alone(harness: Harness) -> None:
    same = stale_run(harness, head_sha=TIP)
    result = supersede(harness)
    assert result.stdout == f"left alone: run {same} (same commit)\n"
    assert harness.gh.writes() == []
    # Whichever commit is being planned decides: for another tip the same run is stale.
    result = supersede(harness, sha=OTHER_SHA)
    assert result.stdout == f"cancelled run {same} (planned {SHA7})\n"


def test_supersede_leaves_alone_a_run_not_on_main_or_not_a_dispatch(harness: Harness) -> None:
    other_branch = stale_run(harness, head_branch="feature")
    prefixed = stale_run(harness, head_branch="main2")
    pushed = stale_run(harness, event="push")
    chained = stale_run(harness, event="workflow_run")
    result = supersede(harness)

    assert result.stdout.splitlines() == [
        f"left alone: run {chained} (not a dispatch)",
        f"left alone: run {pushed} (not a dispatch)",
        f"left alone: run {prefixed} (not on main)",
        f"left alone: run {other_branch} (not on main)",
    ]
    assert harness.gh.writes() == []


@pytest.mark.parametrize(
    "override",
    [
        {"head_sha": None},
        {"head_sha": MISSING_FIELD},
        {"head_sha": OTHER_SHA[:39]},
        {"head_sha": "z" * 40},
        {"head_sha": OTHER_SHA.upper().replace("F", "A")},
        {"head_sha": 12345},
        {"head_sha": OTHER_SHA + "\n"},
        {"triggering_actor": None},
        {"triggering_actor": MISSING_FIELD},
        {"triggering_actor": "github-actions[bot]"},
        {"triggering_actor": {"login": None}},
        {"triggering_actor": {"login": 5}},
        {"triggering_actor": {"id": 41898282}},
        {"triggering_actor": {"login": "has space"}},
        {"triggering_actor": {"login": "line\nbreak"}},
        {"triggering_actor": {"login": "::error::x"}},
        {"event": None},
        {"event": MISSING_FIELD},
        {"event": 7},
        {"head_branch": None},
        {"head_branch": MISSING_FIELD},
        {"head_branch": 7},
        {"status": None},
        {"status": MISSING_FIELD},
    ],
    ids=lambda o: "-".join(f"{k}={str(v)[:12]!r}" for k, v in o.items()),
)
def test_supersede_leaves_alone_a_run_with_a_null_or_missing_field(
    harness: Harness, override: dict[str, object]
) -> None:
    # Every other condition holds, so a null that reached a comparison (in jq `null != $sha` is
    # true) would cancel the run.
    run_id = stale_run(harness, override=override)
    result = supersede(harness)
    assert result.stdout == f"left alone: run {run_id} (unreadable fields)\n"
    assert (result.returncode, result.stderr) == (0, "")
    assert harness.gh.writes() == []
    assert status_of(harness, run_id) == ("waiting", None)


def _jobs_json(second: dict[str, object]) -> str:
    plan = {"name": "plan", "status": "completed", "conclusion": "success"}
    return json.dumps({"jobs": [plan, second]})


@pytest.mark.parametrize(
    "jobs",
    [
        '{"jobs": null}',
        '{"jobs": {}}',
        "{}",
        "not json",
        '{"jobs": ["x"]}',
        _jobs_json({"name": None, "status": "waiting", "conclusion": None}),
        _jobs_json({"name": "apply", "status": None, "conclusion": None}),
        _jobs_json({"name": "apply", "status": "waiting", "conclusion": 3}),
        _jobs_json({"name": "apply", "status": "waiting"}),
    ],
)
def test_supersede_leaves_alone_a_run_whose_jobs_cannot_be_read(
    harness: Harness, jobs: str
) -> None:
    run_id = stale_run(harness)
    env = hook(
        harness,
        {"match": rf"runs/{run_id}/jobs$", "nth": 1, "action": "print", "stdout": jobs},
    )
    result = supersede(harness, env=env)
    assert result.stdout == f"left alone: run {run_id} (unreadable fields)\n"
    assert (result.returncode, result.stderr) == (0, "")
    assert harness.gh.writes() == []


@pytest.mark.parametrize("conclusion", ["success", "cancelled"])
def test_supersede_never_cancels_an_apply_that_reads_waiting_but_has_a_conclusion(
    harness: Harness, conclusion: str
) -> None:
    # GitHub never reports this combination (the fake refuses to plant it), but the script must
    # not take `waiting` alone for a pending approval: a conclusion means the job is over.
    run_id = stale_run(harness)
    jobs = _jobs_json({"name": "apply", "status": "waiting", "conclusion": conclusion})
    env = hook(
        harness, {"match": rf"runs/{run_id}/jobs$", "nth": 1, "action": "print", "stdout": jobs}
    )
    result = supersede(harness, env=env)
    assert result.stdout == f"left alone: run {run_id} (apply is not waiting)\n"
    assert (result.returncode, result.stderr) == (0, "")
    assert harness.gh.writes() == []


def test_supersede_fails_when_the_jobs_cannot_be_fetched(harness: Harness) -> None:
    run_id = stale_run(harness)
    env = hook(harness, {"match": rf"runs/{run_id}/jobs$", "nth": 1, "action": "fail"})
    result = supersede(harness, env=env)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == f"could not read the jobs of run {run_id} from GitHub\n"
    assert harness.gh.writes() == []


def test_supersede_stops_when_the_run_changed_before_the_cancel(harness: Harness) -> None:
    # An approval lands between the listing and the cancel: the fresh read says in progress.
    run_id = stale_run(harness)
    env = hook(
        harness,
        {
            "match": rf"runs/{run_id}$",
            "nth": 1,
            "action": "patch",
            "run": run_id,
            "set": {"status": "in_progress"},
            "jobs": {"apply": ["in_progress", None]},
        },
    )
    result = supersede(harness, env=env)
    assert result.stdout == f"run {run_id} moved on; left alone\n"
    assert (result.returncode, result.stderr) == (0, "")
    assert cancels(harness) == []
    assert status_of(harness, run_id) == ("in_progress", None)


@pytest.mark.parametrize(
    "patch",
    [
        {"set": {}, "jobs": {"apply": ["in_progress", None]}},
        {"set": {}, "jobs": {"apply": ["completed", "success"]}},
    ],
    ids=["apply in progress", "apply done"],
)
def test_supersede_rechecks_run_and_jobs_not_just_the_listing(
    harness: Harness, patch: dict[str, object]
) -> None:
    run_id = stale_run(harness)
    rule = {"match": rf"runs/{run_id}/jobs$", "nth": 2, "action": "patch", "run": run_id, **patch}
    result = supersede(harness, env=hook(harness, rule))
    assert result.stdout == f"run {run_id} moved on; left alone\n"
    assert cancels(harness) == []


def test_supersede_stops_when_the_fresh_read_of_the_run_fails(harness: Harness) -> None:
    run_id = stale_run(harness)
    env = hook(harness, {"match": rf"runs/{run_id}$", "nth": 1, "action": "fail"})
    result = supersede(harness, env=env)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == f"could not read run {run_id} from GitHub\n"
    assert cancels(harness) == []


def test_supersede_rereads_once_after_a_refused_cancel_and_never_loops(
    harness: Harness,
) -> None:
    # 409 while the run still waits: a warning, exit 0, one cancel and no retry.
    run_id = stale_run(harness)
    harness.gh.set_state(cancel_conflict=True)
    harness.gh.reset_log()
    result = supersede(harness)

    assert result.stdout == f"warning: could not cancel run {run_id}; it stays waiting\n"
    assert (result.returncode, result.stderr) == (0, "")
    assert cancels(harness) == [f"api POST repos/{REPO}/actions/runs/{run_id}/cancel"]
    assert run_reads(harness, run_id) == 2  # before the cancel, and once after its refusal
    assert status_of(harness, run_id) == ("waiting", None)


def test_supersede_says_moved_on_when_the_refused_cancel_met_a_finished_run(
    harness: Harness,
) -> None:
    run_id = stale_run(harness)
    env = hook(
        harness,
        {
            "match": rf"POST .*runs/{run_id}/cancel$",
            "nth": 1,
            "action": "patch",
            "run": run_id,
            "set": {"status": "completed", "conclusion": "success"},
            "jobs": {"apply": ["completed", "success"]},
        },
    )
    result = supersede(harness, env=env)
    assert result.stdout == f"run {run_id} moved on; left alone\n"
    assert (result.returncode, result.stderr) == (0, "")
    assert len(cancels(harness)) == 1 and run_reads(harness, run_id) == 2
    assert status_of(harness, run_id) == ("completed", "success")


def test_supersede_goes_on_with_the_next_run_after_a_refused_cancel(harness: Harness) -> None:
    first = stale_run(harness)
    second = stale_run(harness, head_sha="e" * 40)
    env = hook(harness, {"match": rf"POST .*runs/{second}/cancel$", "nth": 1, "action": "fail"})
    result = supersede(harness, env=env)
    assert result.stdout.splitlines() == [
        f"warning: could not cancel run {second}; it stays waiting",
        f"cancelled run {first} (planned fffffff)",
    ]
    assert result.returncode == 0
    assert status_of(harness, first) == ("completed", "cancelled")
    assert status_of(harness, second) == ("waiting", None)


def test_supersede_warns_when_the_token_may_not_cancel(harness: Harness) -> None:
    run_id = stale_run(harness)
    harness.gh.set_state(actions_write=False)
    result = supersede(harness)
    assert result.stdout == f"warning: could not cancel run {run_id}; it stays waiting\n"
    assert result.returncode == 0


def test_supersede_fails_on_an_unreadable_listing(harness: Harness, tmp_path: Path) -> None:
    harness.gh.set_state(logged_out=True)
    result = supersede(harness)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "could not read the deploy-production runs from GitHub\n"
    harness.gh.set_state(logged_out=False)

    fixed = tmp_path / "fixed" / "gh"
    fixed.parent.mkdir()
    fixed.write_text(FIXED_GH)
    fixed.chmod(0o755)
    path = f"{fixed.parent}:{harness.gh.environment()['PATH']}"
    for listing in (
        "",
        "not json",
        "{}",
        "[]",
        '{"workflow_runs": null}',
        '{"workflow_runs": ["x"]}',
        '{"workflow_runs": [{"status": "waiting"}]}',
        '{"workflow_runs": [{"id": "1; rm", "status": "waiting"}]}',
        '{"workflow_runs": [{"id": null}]}',
    ):
        result = supersede(harness, env={"PATH": path, "FIXED_GH_OUT": listing})
        assert (result.returncode, result.stdout) == (1, ""), listing
        assert result.stderr == "unexpected answer for the deploy-production runs\n", listing
    result = supersede(harness, env={"PATH": path, "FIXED_GH_OUT": "{}", "FIXED_GH_STATUS": "1"})
    assert result.stderr == "could not read the deploy-production runs from GitHub\n"
    assert harness.gh.writes() == []


@pytest.mark.parametrize(
    "sha", ["", "abc", TIP[:39], TIP + "0", TIP.upper(), "z" * 40, TIP + "\n", "HEAD", "main"]
)
def test_supersede_validates_its_arguments_before_any_call(harness: Harness, sha: str) -> None:
    result = supersede(harness, sha=sha)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "the sha must be 40 lowercase hexadecimal characters\n"
    assert harness.gh.commands() == []


def test_supersede_validates_its_environment(harness: Harness) -> None:
    result = supersede(harness, env={"REPOSITORY": "a b"})
    assert (result.returncode, result.stderr) == (1, "REPOSITORY must be set to owner/name\n")
    result = supersede(harness, env={"GH_TOKEN": ""})
    assert (result.returncode, result.stderr) == (1, "GH_TOKEN must be set\n")
    assert harness.gh.commands() == []


def test_supersede_makes_only_the_listing_job_run_and_cancel_calls(harness: Harness) -> None:
    stale_run(harness)
    stale_run(harness, actor="reviewer-a")
    stale_run(harness, head_sha=TIP)
    stale_run(harness, override={"head_sha": None})
    stale_run(harness, jobs=[("plan", "completed", "success"), ("apply", "in_progress", None)])
    harness.gh.reset_log()
    supersede(harness)

    allowed = (
        re.escape(f"api GET {RUNS}?status=waiting&per_page=100"),
        re.escape(f"api GET repos/{REPO}/actions/runs/") + r"[0-9]+(/jobs)?",
        re.escape(f"api POST repos/{REPO}/actions/runs/") + r"[0-9]+/cancel",
    )
    for command in harness.gh.commands():
        assert any(re.fullmatch(pattern, command) for pattern in allowed), command
    assert [c["argv"][0] for c in harness.gh.writes()] == ["api"]
    assert all(c["argv"][1:3] == ["--method", "POST"] for c in harness.gh.writes())
    assert len(harness.gh.writes()) == 1
    assert "pending_deployments" not in json.dumps(harness.gh.calls())


def test_start_dispatches_without_inputs_and_prints_the_new_run(harness: Harness) -> None:
    before = harness.gh.add_run(status="waiting", conclusion=None, actor=BOT_LOGIN)
    done = harness.gh.add_run()
    harness.gh.reset_log()
    result = start(harness)

    new = max(r["id"] for r in harness.gh.state()["runs"])
    assert new not in (before, done)
    assert result.stdout == (
        f"started deploy-production run {new}: https://github.com/{REPO}/actions/runs/{new}\n"
    )
    assert (result.returncode, result.stderr) == (0, "")
    argvs = [c["argv"] for c in harness.gh.calls()]
    assert DISPATCH in argvs
    dispatch = argvs[argvs.index(DISPATCH)]
    assert dispatch == DISPATCH
    # No input of any kind: not -f, not --field, not --raw-field, not sha.
    assert not any(
        arg.startswith(("-f", "-F", "--field", "--raw-field")) or "sha" in arg for arg in dispatch
    )
    assert harness.gh.commands()[0] == START_LIST
    assert harness.gh.commands()[1] == "workflow run"
    assert [c["argv"][:2] for c in harness.gh.writes()] == [["workflow", "run"]]
    assert harness.gh.state()["runs"][-1]["inputs"] == {}
    assert TOKEN not in result.stdout + result.stderr


def test_start_ignores_runs_that_existed_before_the_dispatch(harness: Harness) -> None:
    # Old runs, even a waiting one made a moment ago by the bot, are not the new run.
    old = [
        harness.gh.add_run(status="waiting", conclusion=None, actor=BOT_LOGIN),
        harness.gh.add_run(status="queued", conclusion=None, actor=BOT_LOGIN),
        harness.gh.add_run(),
    ]
    harness.gh.set_state(dispatch_no_run=True)
    result = start(harness, env={"START_WAIT_SECONDS": "3"})
    assert result.returncode == 1 and result.stdout == ""
    assert "no deploy-production run started" in result.stderr
    harness.gh.set_state(dispatch_no_run=False)
    result = start(harness)
    new = max(r["id"] for r in harness.gh.state()["runs"])
    assert new not in old
    assert result.stdout.startswith(f"started deploy-production run {new}: ")


def test_start_fails_red_when_the_dispatch_creates_no_run(harness: Harness) -> None:
    harness.gh.set_state(dispatch_no_run=True)
    result = start(harness, env={"START_WAIT_SECONDS": "2", "START_POLL_SECONDS": "1"})
    assert result.returncode == 1 and result.stdout == ""
    assert result.stderr == (
        "the dispatch was accepted but no deploy-production run started "
        "(check that workflow_run chaining is not limited); "
        "look for a run before starting another\n"
    )
    # The dispatch was made once, then the list was read again a bounded number of times.
    commands = harness.gh.commands()
    assert commands.count("workflow run") == 1
    assert 2 <= commands.count(START_LIST) - 1 <= 4


def test_start_polls_a_bounded_number_of_times_with_a_zero_sleep(harness: Harness) -> None:
    harness.gh.set_state(dispatch_no_run=True)
    result = start(harness, env={"START_WAIT_SECONDS": "5", "START_POLL_SECONDS": "0"})
    assert result.returncode == 1
    reads_after = harness.gh.commands().count(START_LIST) - 1
    assert 1 <= reads_after <= 10


def test_start_reads_again_when_the_wait_is_shorter_than_one_poll(harness: Harness) -> None:
    # 1 second of waiting with a poll every 2 seconds is one more read after one poll (the wait is
    # divided rounding up), not zero: the run that shows up on the second read is found.
    harness.gh.set_state(dispatch_lag=1)
    result = start(harness, env={"START_WAIT_SECONDS": "1", "START_POLL_SECONDS": "2"})
    new = max(r["id"] for r in harness.gh.state()["runs"])
    assert (result.returncode, result.stderr) == (0, "")
    assert result.stdout.startswith(f"started deploy-production run {new}: ")
    assert harness.gh.commands().count(START_LIST) == 3


def test_start_with_no_wait_reads_once_and_never_sleeps(harness: Harness) -> None:
    harness.gh.set_state(dispatch_no_run=True)
    result = start(harness, env={"START_WAIT_SECONDS": "0", "START_POLL_SECONDS": "5"})
    assert result.returncode == 1 and "no deploy-production run started" in result.stderr
    assert harness.gh.commands().count(START_LIST) == 2


def test_start_waits_for_a_run_that_appears_late(harness: Harness) -> None:
    harness.gh.set_state(dispatch_lag=3)
    result = start(harness, env={"START_WAIT_SECONDS": "10", "START_POLL_SECONDS": "0"})
    new = max(r["id"] for r in harness.gh.state()["runs"])
    assert (result.returncode, result.stderr) == (0, "")
    assert result.stdout.startswith(f"started deploy-production run {new}: ")
    # Too late for the bound: red.
    harness.gh.set_state(dispatch_lag=50)
    result = start(harness, env={"START_WAIT_SECONDS": "3", "START_POLL_SECONDS": "0"})
    assert result.returncode == 1 and "no deploy-production run started" in result.stderr


def test_start_fails_when_the_token_may_not_dispatch(harness: Harness) -> None:
    harness.gh.set_state(actions_write=False)
    harness.gh.reset_log()
    result = start(harness)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr.startswith("could not create workflow dispatch event: HTTP 403: ")
    assert result.stderr.count("\n") == 1
    assert harness.gh.commands() == [START_LIST, "workflow run"]


@pytest.mark.parametrize(
    "message",
    ["::error::boom\n", "bad\x1b[31m\n", "tab\there\n", "", "é\n"],
)
def test_start_never_echoes_a_dispatch_error_that_is_not_plain_text(
    harness: Harness, tmp_path: Path, message: str
) -> None:
    fixed = tmp_path / "bin2" / "gh"
    fixed.parent.mkdir()
    fixed.write_text(
        "#!/bin/bash\n"
        'if [[ "$1" == workflow ]]; then printf \'%b\' "$ERR" >&2; exit 1; fi\n'
        f'exec "{harness.gh.bin / "gh"}" "$@"\n'
    )
    fixed.chmod(0o755)
    path = f"{fixed.parent}:{harness.gh.environment()['PATH']}"
    result = start(harness, env={"PATH": path, "ERR": message})
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "the dispatch was refused\n"


def test_start_does_not_dispatch_when_the_run_list_cannot_be_read(harness: Harness) -> None:
    harness.gh.set_state(logged_out=True)
    result = start(harness)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "could not read the deploy-production runs from GitHub\n"
    assert harness.gh.writes() == []


def test_start_fails_when_the_run_list_after_the_dispatch_cannot_be_read(
    harness: Harness,
) -> None:
    env = hook(harness, {"match": r"runs\?event=", "nth": 2, "action": "fail"})
    result = start(harness, env=env)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == (
        "could not read the deploy-production runs from GitHub; the dispatch was already sent, "
        "look for a run before starting another\n"
    )


@pytest.mark.parametrize(
    "listing",
    ["", "not json", "{}", '{"workflow_runs": null}', '{"workflow_runs": [{"id": "x"}]}'],
)
def test_start_refuses_a_run_list_it_cannot_read(harness: Harness, listing: str) -> None:
    env = hook(harness, {"match": r"runs\?event=", "nth": 1, "action": "print", "stdout": listing})
    result = start(harness, env=env)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "unexpected answer for the deploy-production runs\n"
    assert harness.gh.writes() == []


@pytest.mark.parametrize(
    "setting",
    [
        {"START_WAIT_SECONDS": "abc"},
        {"START_WAIT_SECONDS": "05"},
        {"START_POLL_SECONDS": "007"},
        {"START_WAIT_SECONDS": "1000"},
        {"START_WAIT_SECONDS": "-1"},
        {"START_WAIT_SECONDS": "9.5"},
        {"START_WAIT_SECONDS": "1 2"},
        {"START_WAIT_SECONDS": "1\n2"},
        {"START_POLL_SECONDS": "abc"},
        {"START_POLL_SECONDS": "1000"},
        {"START_POLL_SECONDS": "-3"},
    ],
    ids=lambda s: "=".join(next(iter(s.items()))),
)
def test_start_validates_its_numeric_settings(harness: Harness, setting: dict[str, str]) -> None:
    result = start(harness, env=setting)
    assert (result.returncode, result.stdout) == (1, "")
    name = next(iter(setting))
    assert result.stderr == f"{name} must be a whole number of seconds, 0 to 999\n"
    assert harness.gh.commands() == []


NEW_RUN = 7999999999
GOOD_URL = f"https://github.com/{REPO}/actions/runs/{NEW_RUN}"


def listing_with(url: object, run_id: int = NEW_RUN) -> dict[str, object]:
    body = json.dumps({"workflow_runs": [{"id": run_id, "html_url": url}]})
    return {"match": r"runs\?event=", "nth": 2, "action": "print", "stdout": body}


def test_start_prints_the_url_of_the_new_run(harness: Harness) -> None:
    result = start(harness, env=hook(harness, listing_with(GOOD_URL)))
    assert (result.returncode, result.stderr) == (0, "")
    assert result.stdout == f"started deploy-production run {NEW_RUN}: {GOOD_URL}\n"


@pytest.mark.parametrize(
    "url",
    [
        GOOD_URL.replace("https://", "http://"),
        GOOD_URL.replace("github.com", "evil.example"),
        GOOD_URL + "/",
        GOOD_URL + "x",
        GOOD_URL + "\n",
        GOOD_URL + " more",
        GOOD_URL.replace("CSAV2", "CS AV2"),
        f"https://github.com/{REPO}/actions/runs/1",
        "",
        None,
        7,
    ],
)
def test_start_validates_the_url_it_prints(harness: Harness, url: object) -> None:
    result = start(harness, env=hook(harness, listing_with(url)))
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "unexpected answer for the deploy-production runs\n"


def test_start_never_cancels_or_approves(harness: Harness) -> None:
    stale_run(harness)
    harness.gh.reset_log()
    start(harness)
    assert cancels(harness) == []
    assert "pending_deployments" not in json.dumps(harness.gh.calls())


def test_the_actor_marker_is_one_constant_and_start_passes_no_input() -> None:
    text = SCRIPT.read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert code.count('AUTO_ACTOR="github-actions[bot]"') == 1
    lines = [line for line in code.splitlines() if "workflow run" in line]
    assert len(lines) == 1
    assert 'workflow run deploy-production.yml --repo "$REPOSITORY" --ref main' in lines[0]
    assert not re.search(r"(-f|-F|--field|--raw-field|sha)\b", lines[0])
    assert "gh run " not in code


# --- the script itself -----------------------------------------------------------------------


def test_the_resolver_is_the_sibling_script_in_staging_mode() -> None:
    text = SCRIPT.read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert 'resolver="$here/resolve_commit.sh"' in code
    assert "DEPLOY_TARGET=staging" in code and "DEPLOY_TARGET=production" not in code
    assert re.search(r'DEPLOY_TARGET=staging "\$resolver" ""', code)
    assert (SCRIPT.parent / "resolve_commit.sh").is_file()


def test_script_is_bash_strict_quoted_executable_and_never_approves() -> None:
    text = SCRIPT.read_text()
    lines = text.splitlines()
    code = "\n".join(line for line in lines if not line.lstrip().startswith("#"))

    assert lines[0] == "#!/bin/bash"
    strict = lines.index("set -euo pipefail")
    locale = lines.index("export LC_ALL=C")
    assert strict < locale < next(i for i, line in enumerate(lines) if "=~" in line)
    assert not re.search(r"\beval\b|\b(?:ba)?sh -c\b|\bset -x\b|\bsource\b", code)
    assert "jq" in code
    # The auto path can never approve or review a run; comment lines may say "reviewers".
    for needle in ("pending_deployments", "run approve", "gh run review"):
        assert needle not in code, needle
    assert _unquoted_expansions(text) == []
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    tracked = subprocess.run(
        ["git", "ls-files", "-s", str(SCRIPT)], capture_output=True, text=True, cwd=ROOT, check=True
    ).stdout
    assert tracked == "" or tracked.startswith("100755")
