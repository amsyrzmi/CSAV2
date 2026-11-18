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


def apply(
    gh: Gh, target: str, *extra: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return run(gh, target, "--repo", REPO, *extra, env=env)


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
    assert gh.commands() == expected
    assert step_lines(result) == [
        f"[{n}/2] environment {name}: created" for n, name in enumerate(environments, 1)
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
        # The body is the complete desired state, the policy flags included.
        body = put_bodies(gh)[name]
        assert set(body) == BODY_KEYS
        assert body["deployment_branch_policy"] == CUSTOM_POLICIES
        assert body["can_admins_bypass"] is (not production)
    # The write goes through stdin (--input -), never through field flags.
    for call in gh.writes():
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
        result = apply(gh, target, *again)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert gh.writes() == []
        assert {line.split(": ")[-1] for line in step_lines(result)} == {"unchanged"}
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
        assert gh.writes() == []
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
    result = apply(gh, "production", "--reviewer", "REVIEWER-B", "--reviewer", "reviewer-a")
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
            "[2/2] environment production: failed: reviewers is reviewer-a after the write, "
            "expected reviewer-b",
        ),
        (
            "reviewers reset (replace)",
            "replace",
            "reviewers",
            "plan-ok",
            ("--reviewer", "reviewer-b", "--replace-reviewers"),
            "[2/2] environment production: failed: reviewers is none after the write, "
            "expected reviewer-b",
        ),
        (
            "admin bypass (merge)",
            "merge",
            "can_admins_bypass",
            "plan-bypass",
            (),
            "[1/2] environment production-plan: failed: can_admins_bypass is true after the "
            "write, expected false",
        ),
        (
            "admin bypass (replace)",
            "replace",
            "can_admins_bypass",
            "plan-bypass",
            (),
            "[1/2] environment production-plan: failed: can_admins_bypass is true after the "
            "write, expected false",
        ),
        (
            "wait timer reset (replace)",
            "replace",
            "wait_timer",
            "plan-wait",
            (),
            "[1/2] environment production-plan: failed: wait_timer is 0 after the write, "
            "expected 5",
        ),
        (
            "self-review reset (replace)",
            "replace",
            "prevent_self_review",
            "plan-self-review",
            (),
            "[1/2] environment production-plan: failed: prevent_self_review is false after "
            "the write, expected true",
        ),
        (
            "policy flags (merge)",
            "merge",
            "deployment_branch_policy",
            "plan-no-policy",
            (),
            "[1/2] environment production-plan: failed: custom_branch_policies is false after "
            "the write, expected true",
        ),
        (
            "policy flags (replace)",
            "replace",
            "deployment_branch_policy",
            "plan-no-policy",
            (),
            "[1/2] environment production-plan: failed: custom_branch_policies is false after "
            "the write, expected true",
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
    failed = "production-plan" if "[1/2]" in failure else "production"
    assert put_commands(gh)[-1] == f"api PUT repos/{REPO}/environments/{failed}"
    if failed == "production-plan":
        assert put_commands(gh) == [f"api PUT repos/{REPO}/environments/production-plan"]
        assert policies_path("production-plan") not in " ".join(gh.commands())
    assert step_lines(result) == (
        [] if failed == "production-plan" else ["[1/2] environment production-plan: unchanged"]
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
    assert apply(gh, "staging").returncode == 0
    assert policy_names(gh, "staging") == ["main"]
    assert gh.state()["environments"]["staging"]["policies"][0]["type"] == "branch"

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
    assert gh.commands() == [
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
    assert "[1/2] environment production-plan: failed: gh: Validation Failed (HTTP 422)" in (
        result.stderr
    )
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
    assert "[1/2] environment staging branch policy: failed: gh: Forbidden (HTTP 403)" in (
        result.stderr
    )
    # main was created first, and staging-images was never reached.
    assert policy_names(gh, "staging") == ["release/*", "main"]
    # (staging-images was read with every environment, before the first write, and no more.)
    assert [label for label in gh.commands() if "staging-images" in label] == [
        f"api GET repos/{REPO}/environments/staging-images"
    ]
    assert MARKER not in everything(gh, result)


def test_apply_makes_no_secret_or_variable_call_yet(gh: Gh) -> None:
    for target, reviewers in (("staging", []), ("production", ["--reviewer", "reviewer-a"])):
        plant_markers(gh)
        gh.reset_log()
        result = apply(gh, target, *reviewers, "--region", REGION, "--image-namespace", NAMESPACE)
        assert result.returncode == 0, (result.stdout, result.stderr)
        labels = gh.commands()
        assert not [label for label in labels if label.startswith(("secret ", "variable "))]
        assert not [label for label in labels if "actions/variables" in label]
        assert not [label for label in labels if label.split("?")[0].endswith("/secrets")]
        assert MARKER not in everything(gh, result)


@pytest.mark.parametrize("target", ["staging", "production"])
def test_check_after_apply_is_ok_for_the_environment_items(gh: Gh, target: str) -> None:
    extra = (
        ["--reviewer", "reviewer-a", "--reviewer", "reviewer-b"] if target == "production" else []
    )
    assert apply(gh, target, *extra).returncode == 0
    result = check(gh, target, *extra)
    # Only the secrets and the required variables are still missing: apply does not set them yet.
    assert result.returncode == 1
    assert not_ok(result)
    assert all(item.startswith(("secret ", "variable ")) for item in not_ok(result)), not_ok(result)
    for name in TARGETS[target]["environments"]:
        assert results(result)[f"environment {name} branch policy"] == "ok"
        assert results(result)[f"environment {name} reviewers"] == "ok"
        assert results(result)[f"environment {name}"] == "ok"
        if target == "production":
            assert results(result)[f"environment {name} admin bypass"] == "ok"


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
