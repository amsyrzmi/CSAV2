"""deploy/github/setup-environments.sh: validates its inputs and checks the GitHub side of a target.

The script is never run against the real GitHub here. The stateful fake ``gh`` of
``tests/fake_gh.py`` (a small program on ``PATH``) models the environments API, branch policies,
secrets and repository variables. Stand-ins only: the repository ``example-owner/CSAV2``, the
logins ``reviewer-a`` and ``reviewer-b`` and the region ``zz-test-1``.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.fake_gh import REPO, Gh
from tests.test_production_deploy_assets import _unquoted_expansions

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "github" / "setup-environments.sh"

REGION = "zz-test-1"
NAMESPACE = "ghcr.io/example-owner"
# The item is everything before the first ": " (a result may hold one: `ok (unset: off)`).
STEP_LINE = re.compile(r"^\[(\d+)/(\d+)\] (.+?): (.+)$")
POLICY_LIST = "deployment-branch-policies"

# What each target needs on GitHub (the plan's decision 9).
TARGETS: dict[str, dict[str, Any]] = {
    "staging": {
        "environments": ["staging", "staging-images"],
        "secrets": {
            "staging": ["STAGING_DEPLOY_ROLE_ARN", "STAGING_INSTANCE_ID"],
            "staging-images": [],
        },
        "required_variables": ["IMAGE_NAMESPACE", "STAGING_DEPLOY_REGION"],
        "switch": "STAGING_AUTO_PAUSED",
        "region_variable": "STAGING_DEPLOY_REGION",
    },
    "production": {
        "environments": ["production-plan", "production"],
        "secrets": {
            "production-plan": ["PROD_PLAN_ROLE_ARN", "PROD_INSTANCE_ID"],
            "production": ["PROD_APPLY_ROLE_ARN", "PROD_INSTANCE_ID", "PROD_CONSOLE_ORIGIN"],
        },
        "required_variables": ["IMAGE_NAMESPACE", "PROD_DEPLOY_REGION"],
        "switch": "PROD_AUTO_PLAN",
        "region_variable": "PROD_DEPLOY_REGION",
    },
}
# The count of items that are not ok on an empty repository (see the table above).
EMPTY_NOT_OK = {"staging": 8, "production": 14}


@pytest.fixture
def gh(tmp_path: Path) -> Gh:
    return Gh(tmp_path)


def run(gh: Gh, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return gh.run(SCRIPT, *args, env=env)


def check(gh: Gh, target: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return run(gh, target, "--check", "--repo", REPO, *extra)


def complete(
    gh: Gh,
    target: str,
    *,
    reviewers: tuple[str, ...] = ("reviewer-a",),
    switch: str | None = None,
) -> None:
    """Plant a complete target: every environment with the main-only policy, every secret and
    every variable. Production's environments have admin bypass off (the owner's decision)."""
    spec = TARGETS[target]
    for name in spec["environments"]:
        gated = name == "production"
        gh.add_environment(
            name,
            reviewers=reviewers if gated else (),
            can_admins_bypass=name not in ("production", "production-plan"),
            policies=["main"],
            secrets={secret: f"VALUE-OF-{secret}" for secret in spec["secrets"][name]},
        )
    state = gh.state()
    state["variables"]["IMAGE_NAMESPACE"] = NAMESPACE
    state["variables"][spec["region_variable"]] = REGION
    if switch is not None:
        state["variables"][spec["switch"]] = switch
    gh.save(state)


def set_policies(gh: Gh, name: str, patterns: list[str]) -> None:
    """Replace an environment's branch policies, keeping everything else about it."""
    state = gh.state()
    state["environments"][name]["policies"] = [
        {"id": 900 + index, "name": pattern, "type": "branch"}
        for index, pattern in enumerate(patterns)
    ]
    gh.save(state)


def results(result: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """Step lines as ``what -> result``."""
    found = {}
    for line in result.stdout.splitlines():
        match = STEP_LINE.match(line)
        if match:
            found[match.group(3)] = match.group(4)
    return found


def not_ok(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [what for what, state in results(result).items() if not state.startswith("ok")]


# --- validation -----------------------------------------------------------------------------

BASE = ["--check", "--repo", REPO]
BAD_ARGUMENTS: list[list[str]] = [
    [],
    ["bogus", *BASE],
    ["--check", "--repo", REPO],
    ["Staging", *BASE],
    ["staging\n", *BASE],
    ["staging", "--check"],
    ["staging", "--check", "--repo", ""],
    ["staging", "--check", "--repo", "nope"],
    ["staging", "--check", "--repo", "a/b/c"],
    ["staging", "--check", "--repo", "a b/c"],
    ["staging", "--check", "--repo", f"{REPO}\n"],
    ["staging", *BASE, "--region", ""],
    ["staging", *BASE, "--region", "zz-test"],
    ["staging", *BASE, "--region", "ZZ-test-1"],
    ["staging", *BASE, "--region", "zz-test-1\n"],
    ["staging", *BASE, "--region", "zz-test-12"],
    ["staging", *BASE, "--image-namespace", "ghcr.io/Owner"],
    ["staging", *BASE, "--image-namespace", "docker.io/owner"],
    ["staging", *BASE, "--image-namespace", "ghcr.io/owner/"],
    ["staging", *BASE, "--image-namespace", "ghcr.io/"],
    ["staging", *BASE, "--image-namespace", "ghcr.io/owner\n"],
    ["production", *BASE, "--reviewer", "-bad"],
    ["production", *BASE, "--reviewer", "bad-"],
    ["production", *BASE, "--reviewer", "a_b"],
    ["production", *BASE, "--reviewer", ""],
    ["production", *BASE, "--reviewer", "a" * 40],
    ["production", *BASE, "--reviewer", "reviewer-a\n"],
    ["production", *BASE, *[x for n in range(7) for x in ("--reviewer", f"user{n}")]],
    ["production", *BASE, "--reviewer", "reviewer-a", "--reviewer", "reviewer-a"],
    ["production", *BASE, "--reviewer", "reviewer-a", "--reviewer", "Reviewer-A"],
    ["staging", *BASE, "--reviewer", "reviewer-a"],
    ["staging", *BASE, "--auto-plan", "true"],
    ["staging", "--repo", REPO, "--replace-reviewers"],
    ["production", *BASE, "--paused", "true"],
    ["production", *BASE, "--auto-plan", "maybe"],
    ["production", *BASE, "--auto-plan", "True"],
    ["production", *BASE, "--auto-plan", ""],
    ["staging", *BASE, "--paused", "maybe"],
    ["production", *BASE, "--replace-reviewers"],
    ["staging", *BASE, "--check"],
    ["staging", *BASE, "--repo", REPO],
    ["staging", *BASE, "--region", REGION, "--region", REGION],
    ["production", *BASE, "--auto-plan", "true", "--auto-plan", "true"],
    ["staging", *BASE, "--paused", "true", "--paused", "false"],
    ["staging", *BASE, "--bogus"],
    ["staging", *BASE, "--check", "true"],
    ["staging", *BASE, "extra"],
    ["staging", "--repo"],
    ["staging", *BASE, "--region"],
    ["staging", *BASE, "--image-namespace"],
    ["production", *BASE, "--reviewer"],
    ["production", *BASE, "--auto-plan"],
    ["production", "--repo", REPO, "--check", "--replace-reviewers"],
]


@pytest.mark.parametrize("arguments", BAD_ARGUMENTS, ids=range(len(BAD_ARGUMENTS)))
def test_inputs_are_validated_before_any_gh_call(gh: Gh, arguments: list[str]) -> None:
    result = run(gh, *arguments)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stderr.strip() != ""
    assert gh.calls() == []


def test_a_rejected_value_is_not_echoed(gh: Gh) -> None:
    result = run(gh, "production", *BASE, "--reviewer", "bad_login")
    assert result.returncode == 2
    assert "bad_login" not in result.stdout + result.stderr


# --- the admin check ------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["staging", "production"])
def test_a_logged_out_gh_and_a_non_admin_login_stop_before_any_write(gh: Gh, target: str) -> None:
    assert "auth status" not in SCRIPT.read_text()
    complete(gh, target)

    state = gh.state()
    state["logged_out"] = True
    gh.save(state)
    result = check(gh, target)
    assert result.returncode == 1
    assert "gh auth login" in result.stderr
    assert gh.writes() == []
    assert gh.commands() == [f"api GET repos/{REPO}"]

    state["logged_out"] = False
    state["repo"]["admin"] = False
    gh.save(state)
    gh.reset_log()
    result = check(gh, target)
    assert result.returncode == 1
    assert "admin" in result.stderr
    assert gh.writes() == []
    # The permission check is the one call: nothing about environments was read.
    assert gh.commands() == [f"api GET repos/{REPO}"]
    assert result.stdout == ""


def test_the_permission_check_asks_for_the_admin_flag_only(gh: Gh) -> None:
    check(gh, "staging")
    first = gh.calls()[0]["argv"]
    assert first[:2] == ["api", f"repos/{REPO}"]
    assert "--jq" in first and ".permissions.admin" in first[first.index("--jq") + 1]


# --- check is read only ---------------------------------------------------------------------


@pytest.mark.parametrize("target", ["staging", "production"])
@pytest.mark.parametrize("state", ["empty", "complete", "partial", "differing"])
def test_check_makes_only_read_calls(gh: Gh, target: str, state: str) -> None:
    spec = TARGETS[target]
    if state == "complete":
        complete(gh, target)
    elif state == "partial":
        gh.add_environment(spec["environments"][0], policies=[])
    elif state == "differing":
        complete(gh, target)
        gh.add_environment(
            spec["environments"][0], policies=["release/*"], reviewers=("reviewer-b",)
        )
    extra = ["--reviewer", "reviewer-b"] if target == "production" else []
    result = check(gh, target, *extra, "--region", REGION, "--image-namespace", NAMESPACE)
    assert result.returncode in (0, 1), result.stderr
    assert gh.writes() == []
    assert all(label.startswith("api GET ") for label in gh.commands()), gh.commands()
    # Reviewers are compared by login from the environment read: no user lookup is needed.
    assert not [label for label in gh.commands() if "api GET users/" in label]
    assert all(
        "--method" not in call["argv"] and "--input" not in call["argv"] for call in gh.calls()
    )


# --- an empty repository --------------------------------------------------------------------


@pytest.mark.parametrize("target", ["staging", "production"])
def test_check_on_an_empty_repository_reports_every_item_missing_and_exits_1(
    gh: Gh, target: str
) -> None:
    spec = TARGETS[target]
    result = check(gh, target)
    assert result.returncode == 1
    found = results(result)
    for name in spec["environments"]:
        assert found[f"environment {name}"] == "missing"
        assert found[f"environment {name} branch policy"] == "missing"
        gated = name == "production"
        # Only production needs a reviewer; the other environments have nothing to judge.
        assert found[f"environment {name} reviewers"] == ("missing" if gated else "ok")
        for secret in spec["secrets"][name]:
            assert found[f"secret {secret} on {name}"] == "missing"
        if target == "production":
            assert found[f"environment {name} admin bypass"] == "missing"
    for variable in spec["required_variables"]:
        assert found[f"variable {variable}"] == "missing"
    assert found[f"variable {spec['switch']}"] == "ok (unset: off)"
    assert len(not_ok(result)) == EMPTY_NOT_OK[target]
    assert result.stdout.splitlines()[-1] == f"check: {EMPTY_NOT_OK[target]} item(s) not ok"
    # Nothing exists, so no list of a missing environment's policies or secrets was requested.
    reads = [label.split(" ", 2)[2] for label in gh.commands()]
    assert not [path for path in reads if POLICY_LIST in path or path.endswith("/secrets")]


@pytest.mark.parametrize("target", ["staging", "production"])
def test_check_passes_on_a_complete_target(gh: Gh, target: str) -> None:
    complete(gh, target)
    result = check(gh, target)
    assert result.returncode == 0, result.stdout
    assert result.stdout.splitlines()[-1] == "check: everything is ok"
    assert not_ok(result) == []


def test_check_compares_a_given_region_and_namespace_without_printing_them(gh: Gh) -> None:
    complete(gh, "staging")
    result = check(gh, "staging", "--region", REGION, "--image-namespace", NAMESPACE)
    assert result.returncode == 0
    assert REGION not in result.stdout + result.stderr
    assert NAMESPACE not in result.stdout + result.stderr

    other_region = "zz-test-2"
    other_namespace = "ghcr.io/other-owner"
    result = check(gh, "staging", "--region", other_region, "--image-namespace", other_namespace)
    assert result.returncode == 1
    assert results(result)["variable STAGING_DEPLOY_REGION"] == "differs"
    assert results(result)["variable IMAGE_NAMESPACE"] == "differs"
    for value in (REGION, NAMESPACE, other_region, other_namespace):
        assert value not in result.stdout + result.stderr


def test_a_given_region_is_compared_and_an_absent_variable_is_missing(gh: Gh) -> None:
    complete(gh, "production")
    assert check(gh, "production").returncode == 0
    state = gh.state()
    del state["variables"]["PROD_DEPLOY_REGION"]
    gh.save(state)
    result = check(gh, "production", "--region", REGION)
    assert results(result)["variable PROD_DEPLOY_REGION"] == "missing"


# --- branch policies ------------------------------------------------------------------------


def test_check_reports_a_missing_main_policy_and_extra_policies_as_not_ok(gh: Gh) -> None:
    complete(gh, "staging")
    expected = {
        "none": ([], "missing"),
        "only another": (["release/*"], "differs"),
        "main and another": (["main", "release/*"], "differs"),
        "main": (["main"], "ok"),
    }
    for label, (policies, verdict) in expected.items():
        set_policies(gh, "staging", policies)
        result = check(gh, "staging")
        assert results(result)["environment staging branch policy"] == verdict, label
        assert (result.returncode == 0) == (verdict == "ok"), label

    # A tag policy named main is not the branch policy.
    state = gh.state()
    state["environments"]["staging"]["policies"] = [{"id": 99, "name": "main", "type": "tag"}]
    gh.save(state)
    assert results(check(gh, "staging"))["environment staging branch policy"] == "differs"

    # Protected branches only (custom policies off) is not the wanted setting either.
    state = gh.state()
    state["environments"]["staging"].update(
        custom_branch_policies=False, protected_branches=True, policies=[]
    )
    gh.save(state)
    assert results(check(gh, "staging"))["environment staging branch policy"] == "differs"


def test_check_of_a_null_policy_environment_reports_missing_without_listing_and_does_not_crash(
    gh: Gh,
) -> None:
    """The real staging-images today: no custom policies, so its policy list is a 404."""
    complete(gh, "staging")
    gh.add_environment("staging-images", policies=None)
    result = check(gh, "staging")
    assert result.returncode == 1, result.stderr
    assert results(result)["environment staging-images branch policy"] == "missing"
    assert results(result)["environment staging branch policy"] == "ok"
    assert not [label for label in gh.commands() if f"staging-images/{POLICY_LIST}" in label]
    # The listing itself is requested for an environment whose own read says policies are on.
    assert [label for label in gh.commands() if f"environments/staging/{POLICY_LIST}" in label]


# --- admin bypass, reviewers and notes ------------------------------------------------------


@pytest.mark.parametrize("name", ["production", "production-plan"])
def test_check_reports_admin_bypass_true_on_production_and_production_plan_as_differs(
    gh: Gh, name: str
) -> None:
    complete(gh, "production")
    state = gh.state()
    state["environments"][name]["can_admins_bypass"] = True
    gh.save(state)
    result = check(gh, "production")
    assert result.returncode == 1
    assert results(result)[f"environment {name} admin bypass"] == "differs"
    assert not_ok(result) == [f"environment {name} admin bypass"]
    assert f"note: {name} can_admins_bypass=true" in result.stdout


def test_admin_bypass_is_only_noted_on_staging_and_staging_images(gh: Gh) -> None:
    complete(gh, "staging")
    state = gh.state()
    assert state["environments"]["staging"]["can_admins_bypass"] is True
    result = check(gh, "staging")
    assert result.returncode == 0
    assert "environment staging admin bypass" not in results(result)
    assert "environment staging-images admin bypass" not in results(result)
    assert "note: staging can_admins_bypass=true prevent_self_review=false wait_timer=0" in (
        result.stdout
    )
    assert "note: staging-images can_admins_bypass=true" in result.stdout

    complete(gh, "production")
    state = gh.state()
    state["environments"]["production"]["wait_timer"] = 5
    state["environments"]["production"]["prevent_self_review"] = True
    gh.save(state)
    result = check(gh, "production")
    assert result.returncode == 0
    assert "note: production can_admins_bypass=false prevent_self_review=true wait_timer=5" in (
        result.stdout
    )
    assert "note: production-plan can_admins_bypass=false prevent_self_review=false" in (
        result.stdout
    )


def test_check_production_without_a_reviewer_is_not_ok_and_given_reviewers_are_compared(
    gh: Gh,
) -> None:
    complete(gh, "production", reviewers=())
    result = check(gh, "production")
    assert result.returncode == 1
    assert results(result)["environment production reviewers"] == "missing"

    complete(gh, "production", reviewers=("reviewer-a", "reviewer-b"))
    assert check(gh, "production").returncode == 0
    # No --reviewer: the live set is accepted. The same set, in any order and case: ok.
    given = ["--reviewer", "Reviewer-B", "--reviewer", "reviewer-a"]
    assert check(gh, "production", *given).returncode == 0

    result = check(gh, "production", "--reviewer", "reviewer-a")
    assert result.returncode == 1
    assert results(result)["environment production reviewers"] == "differs"
    assert "note: reviewers on production: reviewer-a, reviewer-b -> reviewer-a" in result.stdout

    result = check(gh, "production", "--reviewer", "reviewer-a", "--reviewer", "reviewer-b")
    assert result.returncode == 0
    assert "reviewers on production:" not in result.stdout


def test_check_notes_reviewers_on_other_environments_without_failing(gh: Gh) -> None:
    complete(gh, "staging")
    for name in ("staging", "staging-images"):
        state = gh.state()
        state["environments"][name]["reviewers"] = [{"type": "User", "id": 1001}]
        gh.save(state)
    result = check(gh, "staging")
    assert result.returncode == 0
    assert results(result)["environment staging-images reviewers"] == "ok"
    for name in ("staging", "staging-images"):
        note = (
            f"note: {name} has 1 required reviewer(s): every job that uses it waits on that click"
        )
        assert note in result.stdout.splitlines()

    complete(gh, "production")
    state = gh.state()
    state["environments"]["production-plan"]["reviewers"] = [
        {"type": "User", "id": 1001},
        {"type": "User", "id": 1002},
    ]
    gh.save(state)
    result = check(gh, "production")
    assert result.returncode == 0
    assert "note: production-plan has 2 required reviewer(s)" in result.stdout


# --- secrets and variables ------------------------------------------------------------------


def test_check_lists_secrets_per_environment_and_reports_a_missing_one(gh: Gh) -> None:
    complete(gh, "production")
    state = gh.state()
    del state["environments"]["production"]["secrets"]["PROD_CONSOLE_ORIGIN"]
    # A secret on the other environment does not count for this one.
    state["environments"]["production-plan"]["secrets"]["PROD_CONSOLE_ORIGIN"] = "x"
    gh.save(state)
    result = check(gh, "production")
    assert result.returncode == 1
    assert not_ok(result) == ["secret PROD_CONSOLE_ORIGIN on production"]
    assert results(result)["secret PROD_INSTANCE_ID on production"] == "ok"
    assert results(result)["secret PROD_INSTANCE_ID on production-plan"] == "ok"


def test_check_prints_the_two_switches_and_no_other_value(gh: Gh) -> None:
    marker = "MARKER-VALUE-7731"
    for target in ("staging", "production"):
        spec = TARGETS[target]
        complete(gh, target)
        state = gh.state()
        state["variables"]["IMAGE_NAMESPACE"] = f"{marker}-namespace"
        state["variables"][spec["region_variable"]] = f"{marker}-region"
        state["variables"]["UNRELATED"] = f"{marker}-unrelated"
        for env in state["environments"].values():
            env["secrets"] = {name: f"{marker}-{name}" for name in env["secrets"]}
        gh.save(state)
        switch = spec["switch"]
        for value, shown in (
            ("true", "ok (true)"),
            ("false", "ok (false)"),
            (None, "ok (unset: off)"),
        ):
            state = gh.state()
            if value is None:
                state["variables"].pop(switch, None)
            else:
                state["variables"][switch] = value
            gh.save(state)
            gh.reset_log()
            result = check(gh, target)
            assert result.returncode == 0, result.stdout
            assert results(result)[f"variable {switch}"] == shown
            everything = result.stdout + result.stderr + gh.log_path.read_text()
            assert marker not in everything

        # A value that is neither true nor false is reported, and never printed.
        state["variables"][switch] = f"{marker}-switch"
        gh.save(state)
        result = check(gh, target)
        assert result.returncode == 1
        assert results(result)[f"variable {switch}"].startswith("differs")
        assert marker not in result.stdout + result.stderr


def test_a_given_switch_that_does_not_match_is_differs(gh: Gh) -> None:
    complete(gh, "production", switch="false")
    result = check(gh, "production", "--auto-plan", "true")
    assert result.returncode == 1
    assert results(result)["variable PROD_AUTO_PLAN"] == "differs (false)"
    assert check(gh, "production", "--auto-plan", "false").returncode == 0

    complete(gh, "staging")  # the switch is unset
    result = check(gh, "staging", "--paused", "true")
    assert results(result)["variable STAGING_AUTO_PAUSED"] == "differs (unset: off)"
    assert result.returncode == 1


def test_check_output_keeps_to_the_step_grammar(gh: Gh) -> None:
    for target in ("staging", "production"):
        for planted in (False, True):
            if planted:
                complete(gh, target)
            result = check(gh, target)
            lines = result.stdout.splitlines()
            steps = [STEP_LINE.match(line) for line in lines if STEP_LINE.match(line)]
            others = [line for line in lines if not STEP_LINE.match(line)]
            assert all(line.startswith(("note: ", "check: ")) for line in others), others
            assert lines[-1].startswith("check: ")
            totals = {int(m.group(2)) for m in steps if m}
            assert len(totals) == 1
            assert [int(m.group(1)) for m in steps if m] == list(range(1, totals.pop() + 1))
            assert result.stderr == ""


# --- apply is not there yet -----------------------------------------------------------------


def test_apply_is_not_available_yet(gh: Gh) -> None:
    result = run(gh, "staging", "--repo", REPO)
    assert result.returncode == 2
    assert "apply is added by the next change" in result.stderr
    assert gh.calls() == []


# --- the script itself ----------------------------------------------------------------------


def test_script_is_bash_strict_quoted_and_executable() -> None:
    text = SCRIPT.read_text()
    lines = text.splitlines()
    code = "\n".join(line for line in lines if not line.lstrip().startswith("#"))

    assert lines[0] == "#!/bin/bash"
    assert "auth status" not in text
    strict = lines.index("set -euo pipefail")
    locale = lines.index("export LC_ALL=C")
    assert strict < locale < next(i for i, line in enumerate(lines) if "=~" in line)
    assert not re.search(r"\beval\b|\b(?:ba)?sh -c\b|\bset -x\b|\bsource\b", code)
    assert "jq" in code
    assert _unquoted_expansions(text) == []
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    tracked = subprocess.run(
        ["git", "ls-files", "-s", str(SCRIPT)], capture_output=True, text=True, cwd=ROOT, check=True
    ).stdout
    assert tracked == "" or tracked.startswith("100755")
