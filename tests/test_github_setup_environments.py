"""deploy/github/setup-environments.sh: validates its inputs and checks the GitHub side of a target.

The script is never run against the real GitHub here. The stateful fake ``gh`` of
``tests/fake_gh.py`` (a small program on ``PATH``) models the environments API, branch policies,
secrets and repository variables. Stand-ins only: the repository ``example-owner/CSAV2``, the
logins ``reviewer-a`` and ``reviewer-b`` and the region ``zz-test-1``.
"""

from __future__ import annotations

import os
import pty
import re
import select
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.fake_gh import FAKE_GH, REPO, Gh
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


@pytest.fixture(autouse=True)
def no_ambient_setup_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """A SETUP_<NAME> value in the shell running pytest must never reach the script."""
    for key in [key for key in os.environ if key.startswith("SETUP_")]:
        monkeypatch.delenv(key)


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
    # A call the fake does not model is a bug in the script, never a read.
    assert "fake gh: unsupported" not in result.stderr
    assert gh.writes() == []
    assert all(label.startswith("api GET ") for label in gh.commands()), gh.commands()
    # Reviewers are compared by login from the environment read: no user lookup is needed.
    assert not [label for label in gh.commands() if "api GET users/" in label]
    # No method, no body, no field flag (the fake classifies -f/-F as a POST).
    forbidden = {"--method", "-X", "--input", "-f", "-F", "--field", "--raw-field"}
    assert all(not forbidden & set(call["argv"]) for call in gh.calls())


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

    # Every list read pages (the fake ignores --paginate, real gh does not): the secrets of each
    # environment that has some, the repository variables and the branch policies.
    reads = {
        "secrets": [
            c for c in gh.calls() if any(a.split("?")[0].endswith("/secrets") for a in c["argv"])
        ],
        "variables": [c for c in gh.calls() if any("actions/variables" in a for a in c["argv"])],
        "policies": [c for c in gh.calls() if any(POLICY_LIST in a for a in c["argv"])],
    }
    for kind, calls in reads.items():
        assert calls, kind
        assert all("--paginate" in call["argv"] for call in calls), kind


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


def test_exactly_six_reviewers_are_accepted_and_the_seventh_is_not(gh: Gh) -> None:
    six = [x for n in range(6) for x in ("--reviewer", f"user{n}")]
    result = check(gh, "production", *six)
    assert result.returncode in (0, 1), result.stderr
    assert gh.calls() != []
    gh.reset_log()
    seven = [x for n in range(7) for x in ("--reviewer", f"user{n}")]
    assert check(gh, "production", *seven).returncode == 2
    assert gh.calls() == []


def test_a_mixed_case_live_reviewer_login_matches_the_lowercase_given_one(gh: Gh) -> None:
    state = gh.state()
    state["users"]["Reviewer-C"] = 1003
    gh.save(state)
    complete(gh, "production", reviewers=("Reviewer-C",))
    assert check(gh, "production", "--reviewer", "reviewer-c").returncode == 0
    assert check(gh, "production", "--reviewer", "REVIEWER-C").returncode == 0
    result = check(gh, "production", "--reviewer", "reviewer-a")
    assert results(result)["environment production reviewers"] == "differs"
    assert "note: reviewers on production: reviewer-c -> reviewer-a" in result.stdout


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


# --- apply: environments, reviewers and branch policies ------------------------------------

MODES = ["merge", "replace"]
MARKER = "MARKER-VALUE-4417"
BODY_KEYS = {
    "reviewers",
    "wait_timer",
    "prevent_self_review",
    "can_admins_bypass",
    "deployment_branch_policy",
}
CUSTOM_POLICIES = {"protected_branches": False, "custom_branch_policies": True}


# Valid stand-in values of every secret (the pattern of each is checked by the script).
ACCOUNT = "123456789012"
VALUES = {
    "STAGING_DEPLOY_ROLE_ARN": f"arn:aws:iam::{ACCOUNT}:role/example-staging-deploy",
    "STAGING_INSTANCE_ID": "mi-0123456789abcdef0",
    "PROD_PLAN_ROLE_ARN": f"arn:aws:iam::{ACCOUNT}:role/example-prod-plan",
    "PROD_APPLY_ROLE_ARN": f"arn:aws:iam::{ACCOUNT}:role/example-prod-apply",
    "PROD_INSTANCE_ID": "mi-0fedcba9876543210",
    "PROD_CONSOLE_ORIGIN": "https://console.example.test",
}
VARIABLE_FLAGS = ["--region", REGION, "--image-namespace", NAMESPACE]
# Items one apply reports: its environments, its secrets and its variables (the switch included).
ITEMS = {
    target: len(spec["environments"])
    + sum(len(names) for names in spec["secrets"].values())
    + len(spec["required_variables"])
    + 1
    for target, spec in TARGETS.items()
}


AT_PLAN = f"[1/{ITEMS['production']}] environment production-plan"
AT_PRODUCTION = f"[2/{ITEMS['production']}] environment production"


def setup_values(target: str) -> dict[str, str]:
    """SETUP_<NAME> for every secret the target needs."""
    names = {name for names in TARGETS[target]["secrets"].values() for name in names}
    return {f"SETUP_{name}": VALUES[name] for name in sorted(names)}


def apply(
    gh: Gh,
    target: str,
    *extra: str,
    env: dict[str, str] | None = None,
    secrets: bool = True,
    variables: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Apply with the variable flags and every secret value, unless told otherwise."""
    environment = setup_values(target) if secrets else {}
    environment.update(env or {})
    flags = VARIABLE_FLAGS if variables else []
    return run(gh, target, "--repo", REPO, *flags, *extra, env=environment)


def env_commands(gh: Gh) -> list[str]:
    """The calls about environments and their policies (secret and variable calls left out)."""
    return [
        label
        for label in gh.commands()
        if not label.startswith(("secret ", "variable "))
        and "actions/variables" not in label
        and not label.split("?")[0].endswith("/secrets")
    ]


def env_writes(gh: Gh) -> list[dict[str, Any]]:
    """The environment and policy writes (secret and variable sets left out)."""
    return [call for call in gh.writes() if call["argv"][0] == "api"]


def secret_sets(gh: Gh) -> list[list[str]]:
    return [call["argv"] for call in gh.calls() if call["argv"][:2] == ["secret", "set"]]


def variable_sets(gh: Gh) -> list[list[str]]:
    return [call["argv"] for call in gh.calls() if call["argv"][:2] == ["variable", "set"]]


def env_lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in step_lines(result) if " environment " in line.split(": ")[0]]


def patch_fake(gh: Gh, anchor: str, snippet: str) -> None:
    """Insert a snippet of the fake gh's Python source in front of an anchor line, once."""
    fake = gh.bin / "gh"
    text = fake.read_text()
    assert anchor in text
    fake.write_text(text.replace(anchor, snippet + anchor, 1))


def restore_fake(gh: Gh) -> None:
    (gh.bin / "gh").write_text(FAKE_GH)


def set_mode(gh: Gh, mode: str) -> None:
    state = gh.state()
    state["put_semantics"] = mode
    gh.save(state)


def put_bodies(gh: Gh) -> dict[str, dict[str, Any]]:
    """The body of each accepted environment PUT, by environment name (the last one wins)."""
    return {b["path"]: b["body"] for b in gh.api_bodies() if b["method"] == "PUT"}


def put_commands(gh: Gh) -> list[str]:
    return [label for label in gh.commands() if label.startswith("api PUT ")]


def policy_names(gh: Gh, name: str) -> list[str]:
    return [policy["name"] for policy in gh.state()["environments"][name]["policies"]]


def reviewer_ids(gh: Gh, name: str) -> list[int]:
    return sorted(item["id"] for item in gh.state()["environments"][name]["reviewers"])


def policies_path(name: str) -> str:
    return f"repos/{REPO}/environments/{name}/{POLICY_LIST}"


def fresh_gh(gh: Gh) -> Gh:
    """A second, empty fake repository next to this one."""
    directory = gh.tmp.parent / "fresh"
    directory.mkdir(exist_ok=True)
    return Gh(directory)


def plant_markers(gh: Gh) -> None:
    """A marker in every variable and every secret that exists: none may ever be printed."""
    state = gh.state()
    state["variables"]["IMAGE_NAMESPACE"] = f"{MARKER}-namespace"
    state["variables"]["UNRELATED"] = f"{MARKER}-unrelated"
    for env in state["environments"].values():
        env["secrets"] = {name: f"{MARKER}-{name}" for name in env["secrets"]}
    gh.save(state)


def everything(gh: Gh, *results: subprocess.CompletedProcess[str]) -> str:
    return "".join(r.stdout + r.stderr for r in results) + gh.log_path.read_text()


def step_lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in result.stdout.splitlines() if STEP_LINE.match(line)]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("target", ["staging", "production"])
def test_apply_on_an_empty_repository_creates_each_environment_then_its_main_policy_in_order(
    gh: Gh, target: str, mode: str
) -> None:
    set_mode(gh, mode)
    environments = TARGETS[target]["environments"]
    production = target == "production"
    extra = ["--reviewer", "reviewer-a", "--reviewer", "reviewer-b"] if production else []

    result = apply(gh, target, *extra)

    assert result.returncode == 0, (result.stdout, result.stderr)
    # Every environment is read, and both reviewers resolved, before the first write; then, for
    # each environment, the PUT, the re-read, and only then the policy list and the POST: the
    # list of an environment with no custom policies is a 404, so nothing asks for it earlier.
    expected = [f"api GET repos/{REPO}"]
    expected += [f"api GET repos/{REPO}/environments/{name}" for name in environments]
    if production:
        expected += ["api GET users/reviewer-a", "api GET users/reviewer-b"]
    for name in environments:
        path = f"repos/{REPO}/environments/{name}"
        expected += [
            f"api PUT {path}",
            f"api GET {path}",
            f"api GET {policies_path(name)}?per_page=100",
            f"api POST {policies_path(name)}",
        ]
    assert env_commands(gh) == expected
    assert env_lines(result) == [
        f"[{n}/{ITEMS[target]}] environment {name}: created"
        for n, name in enumerate(environments, 1)
    ]

    state = gh.state()
    for name in environments:
        env = state["environments"][name]
        assert policy_names(gh, name) == ["main"]
        assert env["custom_branch_policies"] is True and env["protected_branches"] is False
        assert env["can_admins_bypass"] is (not production)
        assert (env["wait_timer"], env["prevent_self_review"]) == (0, False)
        gated = production and name == "production"
        assert reviewer_ids(gh, name) == ([1001, 1002] if gated else [])
        # The body is the complete desired state, the policy flags included, and every reviewer
        # given is a User entry (a Team-typed entry would be refused or ignored by GitHub).
        body = put_bodies(gh)[name]
        assert set(body) == BODY_KEYS
        assert body["deployment_branch_policy"] == CUSTOM_POLICIES
        assert body["can_admins_bypass"] is (not production)
        wanted = [{"type": "User", "id": 1001}, {"type": "User", "id": 1002}] if gated else []
        assert sorted(body["reviewers"], key=lambda item: item["id"]) == wanted
    # The write goes through stdin (--input -), never through field flags.
    for call in env_writes(gh):
        argv = call["argv"]
        assert argv[argv.index("--input") + 1] == "-" and call["stdin"] > 0
        assert not {"-f", "-F", "--field", "--raw-field"} & set(argv)
    assert "fake gh: unsupported" not in result.stderr


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("target", ["staging", "production"])
def test_apply_twice_makes_no_write_call_the_second_time(gh: Gh, target: str, mode: str) -> None:
    set_mode(gh, mode)
    extra = (
        ["--reviewer", "reviewer-a", "--reviewer", "reviewer-b"] if target == "production" else []
    )
    assert apply(gh, target, *extra).returncode == 0
    before = gh.state()
    for again in (extra, []):  # the same arguments, then with the live reviewers carried over
        gh.reset_log()
        result = apply(gh, target, *again, secrets=False)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert gh.writes() == []
        assert all(state.startswith("unchanged") for state in results(result).values())
        assert len(step_lines(result)) == ITEMS[target]
        assert "note:" not in result.stdout
    after = gh.state()
    assert after["environments"] == before["environments"]


@pytest.mark.parametrize("mode", MODES)
def test_the_put_body_is_the_full_desired_state_and_keeps_what_exists(gh: Gh, mode: str) -> None:
    set_mode(gh, mode)
    gh.add_environment(
        "production",
        reviewers=("reviewer-a", "reviewer-b"),
        wait_timer=5,
        prevent_self_review=True,
        can_admins_bypass=False,
        policies=None,
    )
    result = apply(gh, "production")
    assert result.returncode == 0, (result.stdout, result.stderr)

    body = put_bodies(gh)["production"]
    assert set(body) == BODY_KEYS
    assert sorted(body["reviewers"], key=lambda item: item["id"]) == [
        {"type": "User", "id": 1001},
        {"type": "User", "id": 1002},
    ]
    assert body["wait_timer"] == 5
    assert body["prevent_self_review"] is True
    assert body["can_admins_bypass"] is False
    assert body["deployment_branch_policy"] == CUSTOM_POLICIES
    # Whichever way GitHub treats what a PUT leaves out, nothing was lost.
    env = gh.state()["environments"]["production"]
    assert reviewer_ids(gh, "production") == [1001, 1002]
    assert (env["wait_timer"], env["prevent_self_review"], env["can_admins_bypass"]) == (
        5,
        True,
        False,
    )
    assert policy_names(gh, "production") == ["main"]
    # production-plan did not exist: created, with the defaults of a new environment.
    assert results(result)["environment production-plan"] == "created"
    assert results(result)["environment production"] == "updated"


@pytest.mark.parametrize("mode", MODES)
def test_admin_bypass_is_false_on_production_and_production_plan_and_carried_on_the_others(
    gh: Gh, mode: str
) -> None:
    set_mode(gh, mode)
    for name in ("production-plan", "production"):
        gh.add_environment(
            name,
            reviewers=("reviewer-a",) if name == "production" else (),
            can_admins_bypass=True,
            policies=["main"],
        )
    result = apply(gh, "production")
    assert result.returncode == 0, (result.stdout, result.stderr)
    for name in ("production-plan", "production"):
        assert put_bodies(gh)[name]["can_admins_bypass"] is False
        assert gh.state()["environments"][name]["can_admins_bypass"] is False
        assert f"note: {name} admin bypass: true -> false (changed)" in result.stdout
        assert results(result)[f"environment {name}"] == "updated"
    assert reviewer_ids(gh, "production") == [1001]

    # staging and staging-images keep what they have, true here and false below, and are not
    # written for it.
    for bypass in (True, False):
        gh.reset_log()
        for name in ("staging", "staging-images"):
            gh.add_environment(name, can_admins_bypass=bypass, policies=["main"])
        result = apply(gh, "staging")
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert env_writes(gh) == []
        assert "admin bypass" not in result.stdout
        for name in ("staging", "staging-images"):
            assert gh.state()["environments"][name]["can_admins_bypass"] is bypass

    # A write for another reason (the policy is missing) carries the flag over unchanged.
    gh.add_environment("staging", can_admins_bypass=False, policies=["main"])
    gh.add_environment("staging-images", can_admins_bypass=False, policies=None)
    gh.reset_log()
    result = apply(gh, "staging")
    assert result.returncode == 0
    assert put_commands(gh) == [f"api PUT repos/{REPO}/environments/staging-images"]
    assert put_bodies(gh)["staging-images"]["can_admins_bypass"] is False
    assert "admin bypass" not in result.stdout


def refusal_fixture(gh: Gh, *, reviewers: tuple[str, ...] = ("reviewer-a",)) -> None:
    """An existing production, and a production-plan that needs a write (custom policies off,
    admin bypass on): a refusal must still leave the repository untouched."""
    gh.add_environment("production-plan", can_admins_bypass=True, policies=None)
    gh.add_environment(
        "production", reviewers=reviewers, can_admins_bypass=False, policies=["main"]
    )


def test_a_changed_reviewer_set_on_an_existing_production_is_refused_without_replace_reviewers(
    gh: Gh,
) -> None:
    refusal_fixture(gh)
    plant_markers(gh)
    result = apply(gh, "production", "--reviewer", "reviewer-b")
    assert result.returncode == 1
    assert "note: reviewers on production: reviewer-a -> reviewer-b" in result.stdout
    assert "--replace-reviewers" in result.stderr
    # Nothing was written anywhere, production-plan included.
    assert gh.writes() == []
    assert step_lines(result) == []
    assert MARKER not in everything(gh, result)

    gh.reset_log()
    result = apply(gh, "production", "--reviewer", "reviewer-a", "--reviewer", "reviewer-b")
    assert result.returncode == 1
    assert "reviewers on production: reviewer-a -> reviewer-a, reviewer-b" in result.stdout
    assert gh.writes() == []

    # Giving reviewers to a production that has none is a change too.
    refusal_fixture(gh, reviewers=())
    gh.reset_log()
    result = apply(gh, "production", "--reviewer", "reviewer-a")
    assert result.returncode == 1
    assert "reviewers on production: none -> reviewer-a" in result.stdout
    assert gh.writes() == []


@pytest.mark.parametrize("mode", MODES)
def test_replace_reviewers_applies_the_change_and_prints_old_and_new_logins(
    gh: Gh, mode: str
) -> None:
    set_mode(gh, mode)
    refusal_fixture(gh)
    result = apply(gh, "production", "--reviewer", "reviewer-b", "--replace-reviewers")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "note: reviewers on production: reviewer-a -> reviewer-b" in result.stdout
    assert reviewer_ids(gh, "production") == [1002]
    assert results(result)["environment production"] == "updated"
    assert results(result)["environment production-plan"] == "updated"
    assert gh.state()["environments"]["production-plan"]["can_admins_bypass"] is False


def test_given_reviewers_equal_to_the_live_set_need_no_flag_and_a_new_production_needs_none(
    gh: Gh,
) -> None:
    complete(gh, "production", reviewers=("reviewer-a", "reviewer-b"))
    # The same set, in another order and case: no change, so no flag and no write.
    result = apply(
        gh, "production", "--reviewer", "REVIEWER-B", "--reviewer", "reviewer-a", secrets=False
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert gh.writes() == []
    assert "reviewers on production" not in result.stdout

    fresh = fresh_gh(gh)
    result = apply(fresh, "production", "--reviewer", "reviewer-a")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "--replace-reviewers" not in result.stdout + result.stderr
    assert reviewer_ids(fresh, "production") == [1001]


def test_production_is_refused_without_a_reviewer_when_it_has_none_and_nothing_is_written(
    gh: Gh,
) -> None:
    refusal_fixture(gh, reviewers=())
    result = apply(gh, "production")
    assert result.returncode == 1
    assert "production needs at least one reviewer" in result.stderr
    assert "--reviewer" in result.stderr
    assert gh.writes() == []

    # A production that does not exist yet is refused the same way, before anything is created.
    fresh = fresh_gh(gh)
    result = apply(fresh, "production")
    assert result.returncode == 1
    assert "production needs at least one reviewer" in result.stderr
    assert fresh.writes() == []
    assert fresh.state()["environments"] == {}


def test_an_unknown_or_non_user_reviewer_stops_before_any_write(gh: Gh) -> None:
    refusal_fixture(gh)
    state = gh.state()
    state["users"]["some-org"] = {"id": 2001, "type": "Organization"}
    gh.save(state)
    for login in ("ghost-user", "some-org"):
        gh.reset_log()
        result = apply(gh, "production", "--reviewer", "reviewer-a", "--reviewer", login)
        assert result.returncode == 1, login
        assert login in result.stderr
        assert gh.writes() == []
        # Every environment was read before the lookup, and no environment write came after it.
        assert gh.commands()[1:3] == [
            f"api GET repos/{REPO}/environments/production-plan",
            f"api GET repos/{REPO}/environments/production",
        ]


@pytest.mark.parametrize(
    ("label", "mode", "ignored", "setup", "extra", "failure"),
    [
        (
            "reviewers kept (merge)",
            "merge",
            "reviewers",
            "plan-ok",
            ("--reviewer", "reviewer-b", "--replace-reviewers"),
            f"{AT_PRODUCTION}: failed: reviewers is reviewer-a after the write, "
            "expected reviewer-b",
        ),
        (
            "reviewers reset (replace)",
            "replace",
            "reviewers",
            "plan-ok",
            ("--reviewer", "reviewer-b", "--replace-reviewers"),
            f"{AT_PRODUCTION}: failed: reviewers is none after the write, expected reviewer-b",
        ),
        (
            "admin bypass (merge)",
            "merge",
            "can_admins_bypass",
            "plan-bypass",
            (),
            f"{AT_PLAN}: failed: can_admins_bypass is true after the write, expected false",
        ),
        (
            "admin bypass (replace)",
            "replace",
            "can_admins_bypass",
            "plan-bypass",
            (),
            f"{AT_PLAN}: failed: can_admins_bypass is true after the write, expected false",
        ),
        (
            "wait timer reset (replace)",
            "replace",
            "wait_timer",
            "plan-wait",
            (),
            f"{AT_PLAN}: failed: wait_timer is 0 after the write, expected 5",
        ),
        (
            "self-review reset (replace)",
            "replace",
            "prevent_self_review",
            "plan-self-review",
            (),
            f"{AT_PLAN}: failed: prevent_self_review is false after the write, expected true",
        ),
        (
            "policy flags (merge)",
            "merge",
            "deployment_branch_policy",
            "plan-no-policy",
            (),
            f"{AT_PLAN}: failed: custom_branch_policies is false after the write, expected true",
        ),
        (
            "policy flags (replace)",
            "replace",
            "deployment_branch_policy",
            "plan-no-policy",
            (),
            f"{AT_PLAN}: failed: custom_branch_policies is false after the write, expected true",
        ),
    ],
)
def test_a_put_that_did_not_take_effect_fails_loudly_after_the_re_read(
    gh: Gh,
    label: str,
    mode: str,
    ignored: str,
    setup: str,
    extra: tuple[str, ...],
    failure: str,
) -> None:
    plan: dict[str, Any] = {"can_admins_bypass": False, "policies": ["main"]}
    if setup == "plan-bypass":
        plan["can_admins_bypass"] = True
    elif setup == "plan-wait":
        plan.update(can_admins_bypass=True, wait_timer=5)  # the bypass change forces a write
    elif setup == "plan-self-review":
        plan.update(can_admins_bypass=True, reviewers=("reviewer-a",), prevent_self_review=True)
    elif setup == "plan-no-policy":
        plan["policies"] = None
    gh.add_environment("production-plan", **plan)
    gh.add_environment(
        "production", reviewers=("reviewer-a",), can_admins_bypass=False, policies=["main"]
    )
    plant_markers(gh)
    state = gh.state()
    state["put_semantics"] = mode
    state["put_ignores"] = [ignored]
    gh.save(state)

    result = apply(gh, "production", *extra)

    assert result.returncode == 1, label
    assert failure in result.stderr, (label, result.stderr)
    assert MARKER not in everything(gh, result)
    # The script stopped: nothing after the failed environment was touched.
    failed = "production-plan" if "[1/" in failure else "production"
    assert put_commands(gh)[-1] == f"api PUT repos/{REPO}/environments/{failed}"
    if failed == "production-plan":
        assert put_commands(gh) == [f"api PUT repos/{REPO}/environments/production-plan"]
        assert policies_path("production-plan") not in " ".join(gh.commands())
    assert step_lines(result) == (
        []
        if failed == "production-plan"
        else [f"[1/{ITEMS['production']}] environment production-plan: unchanged"]
    )


def test_extra_branch_policies_are_deleted_only_after_main_exists(gh: Gh) -> None:
    complete(gh, "staging")
    set_policies(gh, "staging", ["release/*", "hotfix/*"])
    gh.reset_log()
    result = apply(gh, "staging")
    assert result.returncode == 0, (result.stdout, result.stderr)

    commands = gh.commands()
    post = commands.index(f"api POST {policies_path('staging')}")
    deletes = [i for i, label in enumerate(commands) if label.startswith("api DELETE ")]
    assert len(deletes) == 2 and all(post < i for i in deletes)
    assert policy_names(gh, "staging") == ["main"]
    assert "note: staging policy main: created" in result.stdout
    assert "note: staging policy release/*: removed" in result.stdout
    assert "note: staging policy hotfix/*: removed" in result.stdout
    assert results(result)["environment staging"] == "updated"
    assert results(result)["environment staging-images"] == "unchanged"
    # Only the policies changed: the environment itself was not rewritten.
    assert put_commands(gh) == []

    # A tag policy named main is not the branch policy: main is created, the tag removed.
    state = gh.state()
    state["environments"]["staging"]["policies"] = [{"id": 800, "name": "main", "type": "tag"}]
    gh.save(state)
    gh.reset_log()
    result = apply(gh, "staging")
    assert result.returncode == 0
    assert policy_names(gh, "staging") == ["main"]
    assert gh.state()["environments"]["staging"]["policies"][0]["type"] == "branch"
    # The note of a removed policy shows its type when it is not a branch policy.
    assert "note: staging policy main: created" in result.stdout
    assert "note: staging policy main (tag): removed" in result.stdout

    # protected-branches on and custom policies off is rewritten to custom policies.
    state = gh.state()
    state["environments"]["staging"].update(
        custom_branch_policies=False, protected_branches=True, policies=[]
    )
    gh.save(state)
    assert apply(gh, "staging").returncode == 0
    env = gh.state()["environments"]["staging"]
    assert (env["custom_branch_policies"], env["protected_branches"]) == (True, False)
    assert policy_names(gh, "staging") == ["main"]


@pytest.mark.parametrize("mode", MODES)
def test_staging_apply_gives_staging_images_the_policy_and_keeps_its_reviewers(
    gh: Gh, mode: str
) -> None:
    """The real repository today: staging-images has no custom policy (its list is a 404)."""
    set_mode(gh, mode)
    gh.add_environment("staging", can_admins_bypass=True, policies=["main"])
    gh.add_environment("staging-images", can_admins_bypass=True, policies=None)
    result = apply(gh, "staging")
    assert result.returncode == 0, (result.stdout, result.stderr)

    path = f"repos/{REPO}/environments/staging-images"
    # staging is already right, so it is only listed; staging-images has no custom policy: the
    # PUT that turns them on, the re-read, and only then the list and the POST.
    assert env_commands(gh) == [
        f"api GET repos/{REPO}",
        f"api GET repos/{REPO}/environments/staging",
        f"api GET {path}",
        f"api GET {policies_path('staging')}?per_page=100",
        f"api PUT {path}",
        f"api GET {path}",
        f"api GET {policies_path('staging-images')}?per_page=100",
        f"api POST {policies_path('staging-images')}",
    ]
    assert policy_names(gh, "staging-images") == ["main"]
    assert reviewer_ids(gh, "staging-images") == []
    assert reviewer_ids(gh, "staging") == []
    assert results(result)["environment staging"] == "unchanged"
    assert results(result)["environment staging-images"] == "updated"

    # An existing reviewer on staging-images is kept (check reports it as a note).
    gh.add_environment("staging-images", reviewers=("reviewer-a",), policies=None)
    result = apply(gh, "staging")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert reviewer_ids(gh, "staging-images") == [1001]
    assert put_bodies(gh)["staging-images"]["reviewers"] == [{"type": "User", "id": 1001}]
    assert policy_names(gh, "staging-images") == ["main"]


def test_a_failed_write_names_the_step_and_exits_1_without_continuing(gh: Gh) -> None:
    # A carried-over reviewer whose id is not an account: GitHub refuses the PUT with a 422.
    gh.add_environment("production-plan", can_admins_bypass=False, policies=["main"])
    state = gh.state()
    state["environments"]["production-plan"]["reviewers"] = [{"type": "User", "id": 9999}]
    state["environments"]["production-plan"]["can_admins_bypass"] = True
    gh.save(state)
    plant_markers(gh)

    result = apply(gh, "production", "--reviewer", "reviewer-a")

    assert result.returncode == 1
    assert f"{AT_PLAN}: failed: gh: Validation Failed (HTTP 422)" in (result.stderr)
    assert put_commands(gh) == [f"api PUT repos/{REPO}/environments/production-plan"]
    assert "production" not in gh.state()["environments"]
    assert MARKER not in everything(gh, result)


def test_a_failed_policy_delete_names_the_environment_and_stops(gh: Gh) -> None:
    complete(gh, "staging")
    set_policies(gh, "staging", ["release/*"])
    # A fake gh that refuses every DELETE the way GitHub refuses a missing permission.
    fake = gh.bin / "gh"
    refuse = (
        'if "DELETE" in argv:\n    sys.stderr.write("gh: Forbidden (HTTP 403)\\n")\n    finish(1)\n'
    )
    fake.write_text(
        fake.read_text().replace('if argv[:1] == ["api"]:', refuse + 'if argv[:1] == ["api"]:', 1)
    )
    plant_markers(gh)

    result = apply(gh, "staging")

    assert result.returncode == 1
    assert (
        f"[1/{ITEMS['staging']}] environment staging branch policy: failed: gh: Forbidden"
        " (HTTP 403) (the environment was written; run again to finish)"
    ) in result.stderr
    # main was created first, and staging-images was never reached.
    assert policy_names(gh, "staging") == ["release/*", "main"]
    # (staging-images was read with every environment, before the first write, and no more.)
    assert [label for label in gh.commands() if "staging-images" in label] == [
        f"api GET repos/{REPO}/environments/staging-images"
    ]
    assert MARKER not in everything(gh, result)


@pytest.mark.parametrize("target", ["staging", "production"])
def test_check_after_apply_is_ok_for_every_item(gh: Gh, target: str) -> None:
    extra = (
        ["--reviewer", "reviewer-a", "--reviewer", "reviewer-b"] if target == "production" else []
    )
    assert apply(gh, target, *extra).returncode == 0
    result = check(gh, target, *extra, *VARIABLE_FLAGS)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not_ok(result) == []
    assert result.stdout.splitlines()[-1] == "check: everything is ok"
    for name in TARGETS[target]["environments"]:
        assert results(result)[f"environment {name} branch policy"] == "ok"
        assert results(result)[f"environment {name} reviewers"] == "ok"
        assert results(result)[f"environment {name}"] == "ok"
        if target == "production":
            assert results(result)[f"environment {name} admin bypass"] == "ok"
    switch = TARGETS[target]["switch"]
    assert results(result)[f"variable {switch}"] == "ok (unset: off)"


# --- apply: the Task 3 review gaps ----------------------------------------------------------


def test_custom_policies_on_with_an_empty_list_need_a_post_and_no_put(gh: Gh) -> None:
    complete(gh, "staging")
    set_policies(gh, "staging-images", [])  # custom policies on, nothing listed: admits nothing
    gh.reset_log()

    result = apply(gh, "staging", secrets=False)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert put_commands(gh) == []
    assert [label for label in gh.commands() if label.startswith("api POST")] == [
        f"api POST {policies_path('staging-images')}"
    ]
    assert policy_names(gh, "staging-images") == ["main"]
    assert results(result)["environment staging-images"] == "updated"
    assert results(result)["environment staging"] == "unchanged"


FINISH = "(the environment was written; run again to finish)"


def test_a_failed_policy_post_after_the_put_says_the_environment_was_written(gh: Gh) -> None:
    patch_fake(
        gh,
        '    parts = path.split("/")\n',
        '    if method == "POST":\n'
        '        sys.stderr.write("gh: Forbidden (HTTP 403)\\n")\n'
        "        finish(1)\n",
    )
    plant_markers(gh)

    result = apply(gh, "staging")

    assert result.returncode == 1
    assert (
        f"[1/{ITEMS['staging']}] environment staging branch policy: failed: gh: Forbidden"
        f" (HTTP 403) {FINISH}"
    ) in result.stderr
    # State is left as it is: staging was created with custom policies on and no policy (which
    # admits no branch at all), and nothing after it was touched.
    env = gh.state()["environments"]
    assert list(env) == ["staging"]
    assert (env["staging"]["custom_branch_policies"], env["staging"]["policies"]) == (True, [])
    assert MARKER not in everything(gh, result)

    # Running again, with a working gh, finishes the job and writes no second PUT for staging.
    restore_fake(gh)
    gh.reset_log()
    result = apply(gh, "staging")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert put_commands(gh) == [f"api PUT repos/{REPO}/environments/staging-images"]
    assert policy_names(gh, "staging") == ["main"]
    assert policy_names(gh, "staging-images") == ["main"]


def test_a_failed_re_read_after_the_put_says_the_environment_was_written(gh: Gh) -> None:
    patch_fake(
        gh,
        '    parts = path.split("/")\n',
        '    puts = [b for b in state.get("api_bodies", []) if b["method"] == "PUT"]\n'
        '    if method == "GET" and puts:\n'
        '        sys.stderr.write("gh: Server Error (HTTP 500)\\n")\n        finish(1)\n',
    )

    result = apply(gh, "staging")

    assert result.returncode == 1
    assert (
        f"[1/{ITEMS['staging']}] environment staging: failed: gh: Server Error (HTTP 500) {FINISH}"
    ) in result.stderr
    assert list(gh.state()["environments"]) == ["staging"]
    assert policy_names(gh, "staging") == []

    restore_fake(gh)
    result = apply(gh, "staging")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert policy_names(gh, "staging") == ["main"]
    assert policy_names(gh, "staging-images") == ["main"]


def test_a_failure_before_any_write_does_not_claim_the_environment_was_written(gh: Gh) -> None:
    gh.add_environment("production-plan", can_admins_bypass=False, policies=["main"])
    state = gh.state()
    state["environments"]["production-plan"]["reviewers"] = [{"type": "User", "id": 9999}]
    state["environments"]["production-plan"]["can_admins_bypass"] = True
    gh.save(state)
    result = apply(gh, "production", "--reviewer", "reviewer-a")
    assert result.returncode == 1
    assert "failed:" in result.stderr and FINISH not in result.stderr


# --- apply: secrets and variables -----------------------------------------------------------


def all_values() -> list[str]:
    return list(VALUES.values())


@pytest.mark.parametrize("target", ["staging", "production"])
def test_each_environment_gets_exactly_its_secrets_and_the_repository_gets_exactly_its_variables(
    gh: Gh, target: str
) -> None:
    spec = TARGETS[target]
    extra = ["--reviewer", "reviewer-a"] if target == "production" else []

    result = apply(gh, target, *extra)

    assert result.returncode == 0, (result.stdout, result.stderr)
    state = gh.state()
    for name in spec["environments"]:
        wanted = {secret: VALUES[secret] for secret in spec["secrets"][name]}
        assert state["environments"][name]["secrets"] == wanted
    assert state["variables"] == {"IMAGE_NAMESPACE": NAMESPACE, spec["region_variable"]: REGION}
    # One line per item, in the order environments, secrets, variables, numbered to the total.
    lines = step_lines(result)
    assert len(lines) == ITEMS[target]
    assert [STEP_LINE.match(line).group(1) for line in lines] == [  # type: ignore[union-attr]
        str(n) for n in range(1, ITEMS[target] + 1)
    ]
    kinds = [line.split("] ")[1].split(" ")[0] for line in lines]
    assert kinds == sorted(kinds, key=["environment", "secret", "variable"].index)
    for name in spec["environments"]:
        for secret in spec["secrets"][name]:
            assert results(result)[f"secret {secret} on {name}"] == "set"
    assert results(result)["variable IMAGE_NAMESPACE"] == "set"
    assert results(result)[f"variable {spec['region_variable']}"] == "set"
    assert results(result)[f"variable {spec['switch']}"] == "unchanged (unset: off)"
    # Secrets are written only after the last environment exists and has its policy.
    commands = gh.commands()
    first_secret = next(i for i, label in enumerate(commands) if label.startswith("secret set"))
    last_policy = max(i for i, label in enumerate(commands) if POLICY_LIST in label)
    assert last_policy < first_secret


@pytest.mark.parametrize("target", ["staging", "production"])
def test_secrets_come_from_setup_variables_and_reach_gh_on_stdin_only(gh: Gh, target: str) -> None:
    extra = ["--reviewer", "reviewer-a"] if target == "production" else []
    result = apply(gh, target, *extra)
    assert result.returncode == 0, (result.stdout, result.stderr)

    sets = secret_sets(gh)
    assert len(sets) == sum(len(names) for names in TARGETS[target]["secrets"].values())
    calls = [call for call in gh.calls() if call["argv"][:2] == ["secret", "set"]]
    for call in calls:
        argv = call["argv"]
        # Exactly the documented shape: name, environment, repository; no value, no --body.
        assert argv[:3] == ["secret", "set", argv[2]]
        assert argv[3:] == ["--env", argv[4], "--repo", REPO]
        assert not {"--body", "-b"} & set(argv)
        assert not any(value in " ".join(argv) for value in all_values())
        # The value went through stdin: its length is logged, never its content.
        assert call["stdin"] == len(VALUES[argv[2]])
    state = gh.state()
    for name, secrets in TARGETS[target]["secrets"].items():
        for secret in secrets:
            assert state["environments"][name]["secrets"][secret] == VALUES[secret]
    assert not any(value in everything(gh, result) for value in all_values())


def run_in_terminal(
    gh: Gh,
    target: str,
    answers: dict[str, str],
    *args: str,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run the script with stdin and stderr on a pseudo-terminal and answer each hidden prompt.

    Returns the exit status, what the terminal showed (the prompts, and any echo of what was
    typed) and the script's stdout. An answer is typed only after its prompt has appeared, so a
    prompt that does not hide the input would echo it into the terminal text.
    """
    master, slave = pty.openpty()
    stdout_path = gh.tmp / "terminal-stdout.txt"
    with stdout_path.open("w") as stdout_file:
        process = subprocess.Popen(
            [str(SCRIPT), target, "--repo", REPO, *args],
            stdin=slave,
            stdout=stdout_file,
            stderr=slave,
            env=gh.environment(env),
            cwd=gh.tmp,
            close_fds=True,
        )
    os.close(slave)
    shown = b""
    answered: set[str] = set()
    deadline = time.monotonic() + 60
    try:
        while True:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:  # the script closed its end
                    break
                if not chunk:
                    break
                shown += chunk
                for name, value in answers.items():
                    if name not in answered and f"Value for {name} ".encode() in shown:
                        time.sleep(0.3)
                        os.write(master, f"{value}\n".encode())
                        answered.add(name)
            elif process.poll() is not None:
                break
            assert time.monotonic() < deadline, "the script is waiting at a prompt: " + repr(shown)
        status = process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
        os.close(master)
    return status, shown.decode(errors="replace"), stdout_path.read_text()


def test_a_hidden_prompt_supplies_a_missing_secret_and_does_not_echo(gh: Gh) -> None:
    typed = {
        "PROD_PLAN_ROLE_ARN": VALUES["PROD_PLAN_ROLE_ARN"],
        "PROD_INSTANCE_ID": VALUES["PROD_INSTANCE_ID"],
        "PROD_APPLY_ROLE_ARN": VALUES["PROD_APPLY_ROLE_ARN"],
        "PROD_CONSOLE_ORIGIN": VALUES["PROD_CONSOLE_ORIGIN"],
    }

    status, shown, stdout = run_in_terminal(
        gh, "production", typed, *VARIABLE_FLAGS, "--reviewer", "reviewer-a"
    )

    assert status == 0, (shown, stdout)
    # The prompt names where the value will be set (PROD_INSTANCE_ID is on two environments).
    assert (
        "Value for PROD_INSTANCE_ID (hidden; Enter skips it; will be set on: "
        "production-plan, production): "
    ) in shown
    assert (
        "Value for PROD_CONSOLE_ORIGIN (hidden; Enter skips it; will be set on: production): "
        in (shown)
    )
    state = gh.state()
    assert state["environments"]["production-plan"]["secrets"] == {
        "PROD_PLAN_ROLE_ARN": VALUES["PROD_PLAN_ROLE_ARN"],
        "PROD_INSTANCE_ID": VALUES["PROD_INSTANCE_ID"],
    }
    assert state["environments"]["production"]["secrets"] == {
        "PROD_APPLY_ROLE_ARN": VALUES["PROD_APPLY_ROLE_ARN"],
        "PROD_INSTANCE_ID": VALUES["PROD_INSTANCE_ID"],
        "PROD_CONSOLE_ORIGIN": VALUES["PROD_CONSOLE_ORIGIN"],
    }
    # One prompt per name: the node id is asked once and used for both environments.
    for name in typed:
        assert shown.count(f"Value for {name} ") == 1, name
    # What was typed is on the terminal nowhere (the prompt hides it), nor in stdout or the log.
    for value in typed.values():
        assert value not in shown
        assert value not in stdout
        assert value not in gh.log_path.read_text()
    assert all(call["argv"][0] != "secret" or "--body" not in call["argv"] for call in gh.calls())


def test_an_empty_answer_at_the_prompt_skips_the_secret_and_a_bad_one_stops_the_run(
    gh: Gh,
) -> None:
    typed = {
        "STAGING_DEPLOY_ROLE_ARN": "",
        "STAGING_INSTANCE_ID": VALUES["STAGING_INSTANCE_ID"],
    }
    status, shown, stdout = run_in_terminal(gh, "staging", typed, *VARIABLE_FLAGS)
    assert status == 1, (shown, stdout)
    assert "secret STAGING_DEPLOY_ROLE_ARN on staging: missing" in stdout
    assert "secret STAGING_INSTANCE_ID on staging: set" in stdout
    assert stdout.splitlines()[-1] == "apply: 1 item(s) still missing"
    assert list(gh.state()["environments"]["staging"]["secrets"]) == ["STAGING_INSTANCE_ID"]

    fresh = fresh_gh(gh)
    bad = "not-a-node-id-4417"
    status, shown, stdout = run_in_terminal(
        fresh,
        "staging",
        {"STAGING_DEPLOY_ROLE_ARN": "", "STAGING_INSTANCE_ID": bad},
        *VARIABLE_FLAGS,
    )
    assert status == 2, (shown, stdout)
    assert "STAGING_INSTANCE_ID is not a valid managed-node id" in shown + stdout
    assert bad not in shown.replace("Value for STAGING_INSTANCE_ID", "") + stdout
    # Nothing was written, not even the environment.
    assert fresh.writes() == []


def test_a_terminal_is_not_prompted_for_a_secret_that_exists(gh: Gh) -> None:
    complete(gh, "staging")
    status, shown, stdout = run_in_terminal(gh, "staging", {}, *VARIABLE_FLAGS)
    assert status == 0, (shown, stdout)
    assert "Value for" not in shown
    assert secret_sets(gh) == []


def test_a_prompted_value_fills_only_the_environment_that_lacks_the_secret(gh: Gh) -> None:
    complete(gh, "production")
    state = gh.state()
    del state["environments"]["production"]["secrets"]["PROD_INSTANCE_ID"]
    gh.save(state)
    typed = "mi-0bbbbbbbbbbbbbbbb"

    status, shown, stdout = run_in_terminal(gh, "production", {"PROD_INSTANCE_ID": typed})

    assert status == 0, (shown, stdout)
    # One prompt, and the value is written where the secret is missing, not where it exists.
    assert shown.count("Value for ") == 1
    assert "(hidden; Enter skips it; will be set on: production): " in shown
    assert [argv[2:5] for argv in secret_sets(gh)] == [["PROD_INSTANCE_ID", "--env", "production"]]
    envs = gh.state()["environments"]
    assert envs["production"]["secrets"]["PROD_INSTANCE_ID"] == typed
    assert envs["production-plan"]["secrets"]["PROD_INSTANCE_ID"] == "VALUE-OF-PROD_INSTANCE_ID"
    assert typed not in shown + stdout + gh.log_path.read_text()


def test_without_a_terminal_a_missing_secret_is_reported_not_prompted_and_the_rest_is_applied(
    gh: Gh,
) -> None:
    # Only one of the two secrets is given; stdin is not a terminal, so nothing can be asked.
    environment = gh.environment({"SETUP_STAGING_INSTANCE_ID": VALUES["STAGING_INSTANCE_ID"]})
    result = subprocess.run(
        [str(SCRIPT), "staging", "--repo", REPO, *VARIABLE_FLAGS],
        capture_output=True,
        text=True,
        env=environment,
        cwd=gh.tmp,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert results(result)["secret STAGING_DEPLOY_ROLE_ARN on staging"] == "missing"
    assert results(result)["secret STAGING_INSTANCE_ID on staging"] == "set"
    assert "note: give SETUP_STAGING_DEPLOY_ROLE_ARN or run on a terminal" in result.stdout
    assert result.stdout.splitlines()[-1] == "apply: 1 item(s) still missing"
    assert "Value for" not in result.stdout + result.stderr
    # The rest was applied: environments, the other secret and the variables.
    state = gh.state()
    assert set(state["environments"]) == {"staging", "staging-images"}
    assert policy_names(gh, "staging-images") == ["main"]
    assert list(state["environments"]["staging"]["secrets"]) == ["STAGING_INSTANCE_ID"]
    assert state["variables"] == {
        "IMAGE_NAMESPACE": NAMESPACE,
        "STAGING_DEPLOY_REGION": REGION,
    }


@pytest.mark.parametrize(
    ("stdin_kind", "stderr_kind"),
    [("pipe", "file"), ("pipe", "pty"), ("pty", "file")],
    ids=["stdin-pipe", "stdin-pipe-stderr-terminal", "stdin-terminal-stderr-file"],
)
def test_a_prompt_needs_both_stdin_and_stderr_to_be_a_terminal(
    gh: Gh, stdin_kind: str, stderr_kind: str
) -> None:
    # The prompt is only offered when stdin AND stderr are terminals. Here exactly one of them (or
    # neither) is: an open pipe nobody writes to or closes (a CI step, a supervisor), or a
    # terminal nobody types at. A read would block for ever, so the script must not prompt.
    # /dev/null would end the read at once and prove nothing. Each case kills the mutants that
    # test only one of the two (`[[ -t 0 ]]`, `[[ -t 2 ]]`).
    stdout_path = gh.tmp / "pipe-stdout.txt"
    stderr_path = gh.tmp / "pipe-stderr.txt"
    master, slave = pty.openpty()
    stdin_arg: int = subprocess.PIPE if stdin_kind == "pipe" else slave
    with stdout_path.open("w") as stdout_file, stderr_path.open("w") as stderr_file:
        process = subprocess.Popen(
            [str(SCRIPT), "staging", "--repo", REPO, *VARIABLE_FLAGS],
            stdin=stdin_arg,
            stdout=stdout_file,
            stderr=slave if stderr_kind == "pty" else stderr_file,
            env=gh.environment(),
            cwd=gh.tmp,
            close_fds=True,
        )
        os.close(slave)
        try:
            status = process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            pytest.fail(
                "the script is waiting for input where stdin and stderr are not both terminals"
            )
        finally:
            if process.stdin is not None:
                process.stdin.close()
            shown = b""
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    shown = os.read(master, 65536)
                except OSError:
                    shown = b""
            os.close(master)
    assert status == 1
    assert stdout_path.read_text().splitlines()[-1] == "apply: 2 item(s) still missing"
    assert "Value for" not in stdout_path.read_text() + stderr_path.read_text() + shown.decode(
        errors="replace"
    )


def test_a_failed_variable_read_ends_the_run_before_any_prompt(gh: Gh) -> None:
    patch_fake(
        gh,
        'if rest == ["actions", "variables"] and method == "GET":',
        'if rest == ["actions", "variables"]:\n'
        '            sys.stderr.write("gh: Server Error (HTTP 500)\\n")\n'
        "            finish(1)\n        ",
    )

    status, shown, stdout = run_in_terminal(
        gh, "staging", {"STAGING_DEPLOY_ROLE_ARN": VALUES["STAGING_DEPLOY_ROLE_ARN"]}
    )

    assert status == 1, (shown, stdout)
    assert "repository variables: failed: gh: Server Error (HTTP 500)" in shown
    # Nothing was asked for, so no typed value can be lost.
    assert "Value for" not in shown


def test_a_failed_secret_list_read_while_deciding_is_a_plain_failure_not_a_numbered_step(
    gh: Gh,
) -> None:
    complete(gh, "staging")
    patch_fake(
        gh,
        'if tail == "/secrets" and method == "GET":',
        'if tail == "/secrets":\n'
        '        sys.stderr.write("gh: Forbidden (HTTP 403)\\n")\n'
        "        finish(1)\n    ",
    )

    result = apply(gh, "staging", secrets=False)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stderr.splitlines()[-1] == (
        "secret STAGING_DEPLOY_ROLE_ARN on staging: failed: gh: Forbidden (HTTP 403)"
    )
    assert "[1/" not in result.stderr + result.stdout


def test_with_no_secret_values_every_secret_is_counted_and_the_variables_are_still_set(
    gh: Gh,
) -> None:
    result = apply(gh, "production", "--reviewer", "reviewer-a", secrets=False)
    assert result.returncode == 1
    assert result.stdout.splitlines()[-1] == "apply: 5 item(s) still missing"
    assert gh.state()["variables"] == {"IMAGE_NAMESPACE": NAMESPACE, "PROD_DEPLOY_REGION": REGION}
    assert secret_sets(gh) == []
    # Without the variable flags the two required variables are missing too.
    fresh = fresh_gh(gh)
    result = apply(fresh, "production", "--reviewer", "reviewer-a", secrets=False, variables=False)
    assert result.returncode == 1
    assert result.stdout.splitlines()[-1] == "apply: 7 item(s) still missing"
    assert results(result)["variable IMAGE_NAMESPACE"] == "missing"
    assert results(result)["variable PROD_DEPLOY_REGION"] == "missing"
    assert "note: give --image-namespace" in result.stdout
    assert "note: give --region" in result.stdout
    assert variable_sets(fresh) == []


def test_an_existing_secret_is_not_prompted_for_or_overwritten_unless_its_setup_variable_is_set(
    gh: Gh,
) -> None:
    complete(gh, "production")
    before = gh.state()["environments"]

    result = apply(gh, "production", secrets=False)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert secret_sets(gh) == []
    assert gh.writes() == []
    assert gh.state()["environments"] == before
    assert all(
        results(result)[f"secret {secret} on {name}"] == "unchanged"
        for name, secrets in TARGETS["production"]["secrets"].items()
        for secret in secrets
    )

    # An explicit SETUP_<NAME> rotates that secret on every environment that has it, and only it.
    rotated = "mi-0aaaaaaaaaaaaaaaa"
    result = apply(gh, "production", secrets=False, env={"SETUP_PROD_INSTANCE_ID": rotated})
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert [argv[2:5] for argv in secret_sets(gh)] == [
        ["PROD_INSTANCE_ID", "--env", "production-plan"],
        ["PROD_INSTANCE_ID", "--env", "production"],
    ]
    state = gh.state()["environments"]
    assert state["production-plan"]["secrets"]["PROD_INSTANCE_ID"] == rotated
    assert state["production"]["secrets"]["PROD_INSTANCE_ID"] == rotated
    assert state["production"]["secrets"]["PROD_APPLY_ROLE_ARN"] == "VALUE-OF-PROD_APPLY_ROLE_ARN"
    # An overwritten existing secret says `rotated` (never `set`, which is a first write), and
    # the value is not printed.
    assert results(result)["secret PROD_INSTANCE_ID on production-plan"] == "rotated"
    assert results(result)["secret PROD_INSTANCE_ID on production"] == "rotated"
    assert results(result)["secret PROD_APPLY_ROLE_ARN on production"] == "unchanged"
    assert rotated not in result.stdout + result.stderr


BAD_SECRETS = [
    ("PROD_APPLY_ROLE_ARN", f"arn:aws:iam::{ACCOUNT}:user/someone", "role ARN"),
    ("PROD_APPLY_ROLE_ARN", f"arn:aws:iam::12345:role/short-account-{MARKER}", "role ARN"),
    ("PROD_APPLY_ROLE_ARN", f"arn:aws:iam::{ACCOUNT}:role/{MARKER} with space", "role ARN"),
    ("PROD_PLAN_ROLE_ARN", f"arn:aws:iam::{ACCOUNT}:role/ok\n{MARKER}", "role ARN"),
    ("PROD_APPLY_ROLE_ARN", f"arn:aws:iam::{ACCOUNT}:role/service-role/example", "role ARN"),
    ("PROD_APPLY_ROLE_ARN", f"arn:aws:iam::{ACCOUNT}:role/{'a' * 65}", "role ARN"),
    ("PROD_INSTANCE_ID", "mi-0123456789abcdeg", "managed-node id"),
    ("PROD_INSTANCE_ID", "mi-0123456789abcdef01", "managed-node id"),
    ("PROD_INSTANCE_ID", "xmi-0123456789abcdef0", "managed-node id"),
    ("PROD_INSTANCE_ID", "mi-0123456789abcdef0 ", "managed-node id"),
    ("PROD_INSTANCE_ID", "mi-0123456789ABCDEF0", "managed-node id"),
    ("PROD_INSTANCE_ID", "i-0123456789abcdef0", "managed-node id"),
    ("PROD_INSTANCE_ID", "", "managed-node id"),
    ("PROD_CONSOLE_ORIGIN", f"https://console.example.test/{MARKER}", "origin"),
    ("PROD_CONSOLE_ORIGIN", "https://console.example.test:8443", "origin"),
    ("PROD_CONSOLE_ORIGIN", "https://console.example.test/", "origin"),
    ("PROD_CONSOLE_ORIGIN", "HTTPS://console.example.test", "origin"),
    ("PROD_CONSOLE_ORIGIN", "http://console.example.test", "origin"),
    ("PROD_CONSOLE_ORIGIN", f"https://console.example.test\n{MARKER}", "origin"),
]


@pytest.mark.parametrize(("name", "value", "kind"), BAD_SECRETS, ids=range(len(BAD_SECRETS)))
def test_secret_values_are_validated_before_any_write_and_never_echoed(
    gh: Gh, name: str, value: str, kind: str
) -> None:
    result = apply(
        gh,
        "production",
        "--reviewer",
        "reviewer-a",
        env={f"SETUP_{name}": value},
    )

    assert result.returncode == 2, (result.stdout, result.stderr)
    assert f"SETUP_{name} is not a valid {kind}" in result.stderr
    assert gh.writes() == []  # not even an environment was created
    assert gh.state()["environments"] == {}
    shown = result.stdout + result.stderr + gh.log_path.read_text()
    assert MARKER not in shown
    if value.strip():
        assert value not in shown


@pytest.mark.parametrize(
    "name",
    ["PROD_APPLY_ROLE_ARN", "PROD_INSTANCE_ID", "PROD_CONSOLE_ORIGIN"],
)
def test_a_very_long_value_is_refused_for_every_kind_of_secret(gh: Gh, name: str) -> None:
    prefix = {
        "PROD_APPLY_ROLE_ARN": f"arn:aws:iam::{ACCOUNT}:role/",
        "PROD_INSTANCE_ID": "mi-0123456789abcdef0",
        "PROD_CONSOLE_ORIGIN": "https://",
    }[name]
    value = prefix + "a" * 120_000

    result = apply(gh, "production", "--reviewer", "reviewer-a", env={f"SETUP_{name}": value})

    assert result.returncode == 2, (result.stdout[:200], result.stderr[:200])
    assert f"SETUP_{name} is not a valid" in result.stderr
    assert gh.writes() == []
    assert "a" * 50 not in result.stdout + result.stderr + gh.log_path.read_text()


def test_the_longest_role_name_is_still_accepted(gh: Gh) -> None:
    role = f"arn:aws:iam::{ACCOUNT}:role/{'a' * 64}"
    result = apply(gh, "staging", env={"SETUP_STAGING_DEPLOY_ROLE_ARN": role})
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert gh.state()["environments"]["staging"]["secrets"]["STAGING_DEPLOY_ROLE_ARN"] == role


# --- a secret is never traced and never exported ---------------------------------------------

TRACE_MARKERS = {
    "SETUP_STAGING_DEPLOY_ROLE_ARN": f"arn:aws:iam::{ACCOUNT}:role/{MARKER}-traced",
    "SETUP_STAGING_INSTANCE_ID": "mi-0badc0ffee1234567",
}


@pytest.mark.parametrize("way", ["bash -x", "SHELLOPTS=xtrace"])
@pytest.mark.parametrize("failing", [False, True], ids=["success", "failing-write"])
def test_a_secret_never_appears_when_the_caller_turns_tracing_on(
    gh: Gh, way: str, failing: bool
) -> None:
    if failing:
        patch_fake(
            gh,
            'elif argv[:2] == ["secret", "set"]:',
            'elif argv[:2] == ["secret", "set"] and True:\n    read_stdin()\n'
            '    sys.stderr.write("gh: Forbidden (HTTP 403)\\n")\n    finish(1)\n',
        )
    command = ["bash", "-x", str(SCRIPT)] if way == "bash -x" else [str(SCRIPT)]
    extra = {"SHELLOPTS": "xtrace"} if way == "SHELLOPTS=xtrace" else {}

    result = subprocess.run(
        [*command, "staging", "--repo", REPO, *VARIABLE_FLAGS],
        capture_output=True,
        text=True,
        env=gh.environment({**TRACE_MARKERS, **extra}),
        cwd=gh.tmp,
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )

    assert result.returncode == (1 if failing else 0), (result.stdout, result.stderr[-2000:])
    # Tracing really was on for the first lines, and was turned off before any value was read.
    assert "set +x" in result.stderr
    for value in TRACE_MARKERS.values():
        assert value not in result.stdout + result.stderr + gh.log_path.read_text()
    assert MARKER not in result.stdout + result.stderr + gh.log_path.read_text()


def record_setup_variables(record: Path, who: str) -> str:
    """Python source that notes which SETUP_ variables the process inherited."""
    return (
        f"with open({str(record)!r}, 'a') as handle:\n"
        "    names = ','.join(k for k in os.environ if k.startswith('SETUP_'))\n"
        f"    handle.write({who!r} + ' ' + names + '\\n')\n"
    )


def wrap_children(gh: Gh, record: Path) -> None:
    """Put a recording wrapper in front of jq, mktemp, grep and rm: each notes which SETUP_
    variables it inherited, then runs the real program."""
    for tool in ("jq", "mktemp", "grep", "rm"):
        real = shutil.which(tool, path=os.environ["PATH"])
        assert real
        wrapper = gh.bin / tool
        wrapper.write_text(
            f"#!{sys.executable}\nimport os, sys\n"
            + record_setup_variables(record, tool)
            + f"os.execv({real!r}, [{real!r}, *sys.argv[1:]])\n"
        )
        wrapper.chmod(0o755)
    patch_fake(gh, "argv = sys.argv[1:]\n", record_setup_variables(record, "gh"))


@pytest.mark.parametrize("mode", ["apply", "check"])
def test_no_setup_variable_stays_in_the_environment_of_any_child_process(
    gh: Gh, tmp_path: Path, mode: str
) -> None:
    record = tmp_path / "children.txt"
    record.write_text("")
    wrap_children(gh, record)
    # SETUP_ (an empty name) is taken out of the environment too, and breaks nothing.
    values = {
        **setup_values("staging"),
        "SETUP_PROD_INSTANCE_ID": VALUES["PROD_INSTANCE_ID"],
        "SETUP_": MARKER,
    }

    if mode == "check":
        result = run(gh, "staging", "--check", "--repo", REPO, env=values)
        assert result.returncode == 1, (result.stdout, result.stderr)
    else:
        result = run(gh, "staging", "--repo", REPO, *VARIABLE_FLAGS, env=values)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert gh.state()["environments"]["staging"]["secrets"] == {
            "STAGING_DEPLOY_ROLE_ARN": VALUES["STAGING_DEPLOY_ROLE_ARN"],
            "STAGING_INSTANCE_ID": VALUES["STAGING_INSTANCE_ID"],
        }

    seen = [line.split(" ") for line in record.read_text().splitlines()]
    assert {tool for tool, _ in seen} >= {"gh", "jq", "mktemp", "rm"}
    assert [(tool, names) for tool, names in seen if names] == []


@pytest.mark.parametrize("mode", ["apply", "check"])
def test_an_exported_setup_variable_with_an_empty_name_is_ignored_not_a_bash_error(
    gh: Gh, mode: str
) -> None:
    # `export SETUP_=x` names no secret: it must not reach bash's array handling (a raw `bad
    # array subscript` error and exit 1) and must not change what the run does.
    if mode == "check":
        result = run(gh, "staging", "--check", "--repo", REPO, env={"SETUP_": MARKER})
        assert result.returncode == 1, (result.stdout, result.stderr)
        assert result.stdout.splitlines()[-1].startswith("check: ")
    else:
        result = apply(gh, "staging", env={"SETUP_": MARKER})
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert result.stdout.splitlines()[-1] == "apply: everything is in place"
    printed = result.stdout + result.stderr
    assert "subscript" not in printed
    assert MARKER not in printed + gh.log_path.read_text()


def test_no_value_reaches_stdout_stderr_or_the_log(gh: Gh) -> None:
    marker = MARKER.lower()
    values = {
        "SETUP_STAGING_DEPLOY_ROLE_ARN": f"arn:aws:iam::{ACCOUNT}:role/{MARKER}-deploy",
        "SETUP_STAGING_INSTANCE_ID": "mi-0123456789abcdef0",
    }
    flags = ["--region", "zz-marker-1", "--image-namespace", f"ghcr.io/{marker}"]
    state = gh.state()
    state["variables"].update(IMAGE_NAMESPACE=f"{MARKER}-old", UNRELATED=f"{MARKER}-unrelated")
    gh.save(state)

    result = run(gh, "staging", "--repo", REPO, *flags, env=values)

    assert result.returncode == 0, (result.stdout, result.stderr)
    # A variable value is public: it is on argv (the log), but no message prints it.
    printed = result.stdout + result.stderr
    secrets_seen = printed + gh.log_path.read_text()
    assert marker not in printed and MARKER not in printed
    assert MARKER not in secrets_seen
    assert values["SETUP_STAGING_INSTANCE_ID"] not in secrets_seen
    assert "UNRELATED" not in printed

    # A failing write is no different: the first error line of gh, no value.
    patch_fake(
        gh,
        'elif argv[:2] == ["secret", "set"]:',
        'elif argv[:2] == ["secret", "set"] and True:\n    read_stdin()\n'
        '    sys.stderr.write("gh: Forbidden (HTTP 403)\\n")\n    finish(1)\n',
    )
    result = run(gh, "staging", "--repo", REPO, *flags, env=values)
    assert result.returncode == 1
    assert (
        f"[3/{ITEMS['staging']}] secret STAGING_DEPLOY_ROLE_ARN on staging: failed: "
        "gh: Forbidden (HTTP 403)"
    ) in result.stderr
    assert MARKER not in result.stdout + result.stderr + gh.log_path.read_text()


def test_variables_are_set_when_given_and_left_alone_when_equal(gh: Gh) -> None:
    state = gh.state()
    state["variables"]["IMAGE_NAMESPACE"] = "ghcr.io/someone-else"
    state["variables"]["UNRELATED"] = f"{MARKER}-unrelated"
    gh.save(state)

    result = apply(gh, "staging")

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert variable_sets(gh) == [
        ["variable", "set", "IMAGE_NAMESPACE", "--repo", REPO, "--body", NAMESPACE],
        ["variable", "set", "STAGING_DEPLOY_REGION", "--repo", REPO, "--body", REGION],
    ]
    # An existing variable that is overwritten says `changed` (no values); an absent one `set`.
    assert results(result)["variable IMAGE_NAMESPACE"] == "changed"
    assert results(result)["variable STAGING_DEPLOY_REGION"] == "set"
    assert gh.state()["variables"]["UNRELATED"] == f"{MARKER}-unrelated"
    assert MARKER not in result.stdout + result.stderr
    assert "someone-else" not in result.stdout + result.stderr

    # Equal values are not written again, and a present variable that is not given is left alone.
    gh.reset_log()
    result = apply(gh, "staging", secrets=False, variables=False)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert variable_sets(gh) == []
    assert results(result)["variable IMAGE_NAMESPACE"] == "unchanged"
    gh.reset_log()
    result = apply(gh, "staging", secrets=False)
    assert variable_sets(gh) == []
    assert results(result)["variable STAGING_DEPLOY_REGION"] == "unchanged"
    # Only the region is given and differs: only the region is written.
    gh.reset_log()
    result = run(gh, "staging", "--repo", REPO, "--region", "zz-other-2")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert variable_sets(gh) == [
        ["variable", "set", "STAGING_DEPLOY_REGION", "--repo", REPO, "--body", "zz-other-2"]
    ]
    assert results(result)["variable STAGING_DEPLOY_REGION"] == "changed"
    assert "zz-other-2" not in result.stdout + result.stderr


@pytest.mark.parametrize("target", ["staging", "production"])
def test_the_switches_are_set_only_when_their_flag_is_given(gh: Gh, target: str) -> None:
    spec = TARGETS[target]
    switch = spec["switch"]
    flag = "--auto-plan" if target == "production" else "--paused"
    extra = ["--reviewer", "reviewer-a"] if target == "production" else []

    # No flag: the variable is not touched, and the line says what it is (unset means off).
    result = apply(gh, target, *extra)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert switch not in gh.state()["variables"]
    assert [argv[2] for argv in variable_sets(gh)] == ["IMAGE_NAMESPACE", spec["region_variable"]]
    assert results(result)[f"variable {switch}"] == "unchanged (unset: off)"

    # The flag sets it, and the line shows the one value that is printed.
    gh.reset_log()
    result = apply(gh, target, *extra, flag, "true", secrets=False)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert variable_sets(gh) == [["variable", "set", switch, "--repo", REPO, "--body", "true"]]
    assert gh.state()["variables"][switch] == "true"
    assert results(result)[f"variable {switch}"] == "set (true)"
    assert results(check(gh, target, *extra, flag, "true"))[f"variable {switch}"] == "ok (true)"

    # The same flag again writes nothing; no flag leaves it alone and shows the live value.
    for again, shown in (([flag, "true"], "true"), ([], "true")):
        gh.reset_log()
        result = apply(gh, target, *extra, *again, secrets=False)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert gh.writes() == []
        assert results(result)[f"variable {switch}"] == f"unchanged ({shown})"

    # A different value is written; an unrecognised live value is reported, never printed.
    gh.reset_log()
    result = apply(gh, target, *extra, flag, "false", secrets=False)
    assert gh.state()["variables"][switch] == "false"
    assert results(result)[f"variable {switch}"] == "set (false)"
    state = gh.state()
    state["variables"][switch] = f"{MARKER}-odd"
    gh.save(state)
    gh.reset_log()
    result = apply(gh, target, *extra, secrets=False)
    assert result.returncode == 0
    assert results(result)[f"variable {switch}"] == "unchanged (not true or false)"
    assert MARKER not in result.stdout + result.stderr
    assert gh.writes() == []


def test_apply_twice_makes_no_write_call_the_second_time_even_with_secret_inputs_absent(
    gh: Gh,
) -> None:
    extra = ["--reviewer", "reviewer-a", "--auto-plan", "true"]
    assert apply(gh, "production", *extra).returncode == 0
    gh.reset_log()

    result = apply(gh, "production", *extra, secrets=False)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert gh.writes() == []
    assert all(state.startswith("unchanged") for state in results(result).values())
    assert result.stdout.splitlines()[-1] == "apply: everything is in place"


# --- the workflow guard ---------------------------------------------------------------------

WORKFLOWS = ROOT / ".github" / "workflows"
# The workflows that run in a deploy environment or read one of its names.
DEPLOY_WORKFLOWS = [
    "deploy-production-auto.yml",
    "deploy-production.yml",
    "deploy-staging.yml",
    "publish-staging-images.yml",
]


def names_read(text: str, context: str) -> set[str]:
    """The names read from ``secrets`` or ``vars`` in a piece of workflow text, in both spellings:
    ``secrets.NAME`` and ``secrets['NAME']`` (or double quotes)."""
    dotted = re.findall(rf"\b{context}\.([A-Z][A-Z0-9_]*)", text)
    indexed = re.findall(rf"\b{context}\[\s*['\"]([A-Z][A-Z0-9_]*)['\"]\s*\]", text)
    return set(dotted) | set(indexed)


def workflow_reads(path: Path) -> tuple[set[str], set[str], set[str]]:
    """The secrets, variables and environments one workflow reads (``GITHUB_TOKEN`` excluded)."""
    text = path.read_text(encoding="utf-8")
    secrets = names_read(text, "secrets") - {"GITHUB_TOKEN"}
    variables = names_read(text, "vars")
    environments: set[str] = set()
    for job in (yaml.safe_load(text).get("jobs") or {}).values():
        environment = job.get("environment")
        if isinstance(environment, dict):
            environment = environment.get("name")
        if isinstance(environment, str):
            environments.add(environment)
    return secrets, variables, environments


def job_secrets(path: Path) -> dict[tuple[str, str], set[str]]:
    """The secrets each job of a workflow reads, by (job id, environment name; "" for none)."""
    jobs = yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs") or {}
    found: dict[tuple[str, str], set[str]] = {}
    for job_id, job in jobs.items():
        environment = job.get("environment") or ""
        if isinstance(environment, dict):
            environment = environment.get("name", "")
        assert isinstance(environment, str), (path.name, job_id)
        # The job as text, once re-serialised: every `if`, `env`, `with` and `run` of it.
        text = yaml.safe_dump(job)
        found[(job_id, environment)] = names_read(text, "secrets") - {"GITHUB_TOKEN"}
    return found


def test_each_job_reads_exactly_the_secrets_the_script_sets_for_its_environment() -> None:
    text = SCRIPT.read_text()
    by_environment: dict[str, set[str]] = {}
    for environment, name in re.findall(r'^\s*"([a-z-]+)\|([A-Z][A-Z_]*)"$', text, re.MULTILINE):
        by_environment.setdefault(environment, set()).add(name)
    assert set(by_environment) == {"staging", "production-plan", "production"}

    jobs = 0
    for name in DEPLOY_WORKFLOWS:
        for (job_id, environment), secrets in job_secrets(WORKFLOWS / name).items():
            jobs += 1
            # A job outside any environment, or in one the script gives no secrets, reads none.
            assert secrets == by_environment.get(environment, set()), (name, job_id, environment)
    assert jobs >= 5


def test_the_secret_and_variable_readers_know_both_spellings() -> None:
    sample = (
        "a: ${{ secrets.ONE }} ${{ secrets['TWO'] }} ${{ secrets[\"THREE\"] }} ${{ vars['FOUR'] }}"
    )
    assert names_read(sample, "secrets") == {"ONE", "TWO", "THREE"}
    assert names_read(sample, "vars") == {"FOUR"}


def test_the_names_in_the_script_match_what_the_workflows_read() -> None:
    text = SCRIPT.read_text()
    # The quoted rows of the three target tables, both targets together.
    script_secrets = set(re.findall(r'^\s*"[a-z-]+\|([A-Z][A-Z_]*)"$', text, re.MULTILINE))
    script_variables = set(
        re.findall(r'^\s*"([A-Z][A-Z_]*)\|(?:namespace|region|switch)\|', text, re.MULTILINE)
    )
    script_environments = set(
        re.findall(r'^\s*"([a-z-]+)\|(?:required|preserved)\|', text, re.MULTILINE)
    )
    assert script_secrets and script_variables and script_environments

    secrets: set[str] = set()
    variables: set[str] = set()
    environments: set[str] = set()
    for name in DEPLOY_WORKFLOWS:
        found = workflow_reads(WORKFLOWS / name)
        secrets |= found[0]
        variables |= found[1]
        environments |= found[2]

    assert secrets == script_secrets
    assert variables == script_variables
    assert environments == script_environments
    # GITLEAKS_LICENSE is read by ci.yml only and is out of scope.
    assert "GITLEAKS_LICENSE" not in script_secrets


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
    # Tracing is switched off before anything is read: a caller's `bash -x` or SHELLOPTS=xtrace
    # must not print a secret (checked by running it in the tests above).
    assert lines[strict + 1] == "set +x"
    assert strict + 1 < locale
    assert code.index("SETUP_") > code.index("set +x")
    assert "jq" in code
    assert _unquoted_expansions(text) == []
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    tracked = subprocess.run(
        ["git", "ls-files", "-s", str(SCRIPT)], capture_output=True, text=True, cwd=ROOT, check=True
    ).stdout
    assert tracked == "" or tracked.startswith("100755")
