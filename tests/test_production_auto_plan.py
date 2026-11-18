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
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.fake_gh import REPO, TIP, Gh
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


@pytest.mark.parametrize("args", [(), ("extra",), ("check", "extra"), ("start",), ("supersede",)])
def test_arguments_are_validated(harness: Harness, args: tuple[str, ...]) -> None:
    result = harness.check(args=args)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr.startswith("usage: auto_plan.sh")
    assert reads(harness) == [] and harness.stub_calls() == []


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
