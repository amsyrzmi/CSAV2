"""tests/fake_gh.py: the stateful fake ``gh`` describes what it says it models.

These tests check the fake against its own description (the observed shapes of GitHub's
environments API), not GitHub against the fake. Stand-ins only.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.fake_gh import REPO, Gh

NOT_FOUND = "gh: Not Found (HTTP 404)"
MODES = ["merge", "replace"]
CUSTOM = {"protected_branches": False, "custom_branch_policies": True}
FULL_BODY: dict[str, Any] = {
    "reviewers": [{"type": "User", "id": 1001}, {"type": "User", "id": 1002}],
    "wait_timer": 5,
    "prevent_self_review": True,
    "can_admins_bypass": False,
    "deployment_branch_policy": None,
}


@pytest.fixture
def gh(tmp_path: Path) -> Gh:
    return Gh(tmp_path)


def put(gh: Gh, name: str, body: dict[str, Any] | None) -> subprocess.CompletedProcess[str]:
    return gh.call(
        "api",
        "--method",
        "PUT",
        f"repos/{REPO}/environments/{name}",
        "--input",
        "-",
        stdin=json.dumps(body),
    )


def get_env(gh: Gh, name: str) -> dict[str, Any]:
    result = gh.call("api", f"repos/{REPO}/environments/{name}")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def rule(env: dict[str, Any], kind: str) -> dict[str, Any] | None:
    for item in env["protection_rules"]:
        if item["type"] == kind:
            return item
    return None


def reviewer_ids(env: dict[str, Any]) -> list[int]:
    found = rule(env, "required_reviewers")
    return [item["reviewer"]["id"] for item in found["reviewers"]] if found else []


def set_mode(gh: Gh, mode: str, ignores: list[str] | None = None) -> None:
    state = gh.state()
    state["put_semantics"] = mode
    state["put_ignores"] = ignores or []
    gh.save(state)


def stored(gh: Gh, name: str) -> dict[str, Any]:
    return gh.state()["environments"][name]


# --- PUT semantics ----------------------------------------------------------------------------


def test_put_in_merge_mode_keeps_what_it_is_not_given(gh: Gh) -> None:
    assert put(gh, "production", FULL_BODY).returncode == 0
    result = put(gh, "production", {"deployment_branch_policy": CUSTOM})
    assert result.returncode == 0
    env = get_env(gh, "production")
    assert reviewer_ids(env) == [1001, 1002]
    assert rule(env, "wait_timer") == {"type": "wait_timer", "wait_timer": 5}
    assert rule(env, "required_reviewers")["prevent_self_review"] is True  # type: ignore[index]
    assert env["can_admins_bypass"] is False
    assert env["deployment_branch_policy"] == CUSTOM
    assert rule(env, "branch_policy") == {"type": "branch_policy"}


def test_put_in_replace_mode_empties_what_it_is_not_given(gh: Gh) -> None:
    assert put(gh, "production", FULL_BODY).returncode == 0
    set_mode(gh, "replace")
    assert put(gh, "production", {"deployment_branch_policy": CUSTOM}).returncode == 0
    env = get_env(gh, "production")
    assert reviewer_ids(env) == []
    assert rule(env, "required_reviewers") is None
    assert rule(env, "wait_timer") is None
    assert env["can_admins_bypass"] is True
    assert stored(gh, "production")["prevent_self_review"] is False
    assert env["deployment_branch_policy"] == CUSTOM
    # A PUT with no policy at all turns custom policies off and clears the policies.
    gh.call(
        "api",
        "--method",
        "POST",
        f"repos/{REPO}/environments/production/deployment-branch-policies",
        "--input",
        "-",
        stdin=json.dumps({"name": "main", "type": "branch"}),
    )
    assert stored(gh, "production")["policies"] != []
    assert put(gh, "production", {"wait_timer": 1}).returncode == 0
    env = get_env(gh, "production")
    assert env["deployment_branch_policy"] is None
    assert stored(gh, "production")["policies"] == []


@pytest.mark.parametrize("mode", MODES)
def test_put_with_a_full_body_keeps_reviewers_and_flags(gh: Gh, mode: str) -> None:
    set_mode(gh, mode)
    body = {**FULL_BODY, "deployment_branch_policy": CUSTOM}
    assert put(gh, "production", body).returncode == 0
    assert (
        put(gh, "production", {**body, "wait_timer": 9, "can_admins_bypass": True}).returncode == 0
    )
    env = get_env(gh, "production")
    assert reviewer_ids(env) == [1001, 1002]
    assert rule(env, "wait_timer") == {"type": "wait_timer", "wait_timer": 9}
    assert rule(env, "required_reviewers")["prevent_self_review"] is True  # type: ignore[index]
    assert env["can_admins_bypass"] is True
    assert env["deployment_branch_policy"] == CUSTOM
    assert put(gh, "production", {**body, "can_admins_bypass": False}).returncode == 0
    assert get_env(gh, "production")["can_admins_bypass"] is False


@pytest.mark.parametrize(
    ("field", "merge_result", "replace_result"),
    [
        ("reviewers", [1001, 1002], []),
        ("wait_timer", 5, 0),
        ("can_admins_bypass", False, True),
    ],
)
def test_put_ignores_treats_the_named_fields_as_absent_from_the_body(
    gh: Gh, field: str, merge_result: Any, replace_result: Any
) -> None:
    for mode, expected in (("merge", merge_result), ("replace", replace_result)):
        name = f"env-{mode}"
        assert put(gh, name, FULL_BODY).returncode == 0
        set_mode(gh, mode, [field])
        # Every field is sent; the named one is ignored, as if it were not in the body.
        sent = {**FULL_BODY, "reviewers": [{"type": "User", "id": 1001}], "wait_timer": 7}
        sent["can_admins_bypass"] = True
        assert put(gh, name, sent).returncode == 0
        env = get_env(gh, name)
        if field == "reviewers":
            actual: Any = reviewer_ids(env)
        elif field == "wait_timer":
            found = rule(env, "wait_timer")
            actual = found["wait_timer"] if found else 0
        else:
            actual = env["can_admins_bypass"]
        assert actual == expected, (mode, field)
        set_mode(gh, "merge")


# --- reading environments ---------------------------------------------------------------------


def test_get_environment_has_the_real_shape_and_a_missing_one_is_a_404(gh: Gh) -> None:
    gh.add_environment(
        "production",
        reviewers=["reviewer-a"],
        wait_timer=3,
        prevent_self_review=True,
        can_admins_bypass=False,
        policies=["main"],
    )
    gh.add_environment("staging-images")
    env = get_env(gh, "production")
    assert env["name"] == "production"
    assert env["can_admins_bypass"] is False
    assert [item["type"] for item in env["protection_rules"]] == [
        "required_reviewers",
        "wait_timer",
        "branch_policy",
    ]
    assert rule(env, "required_reviewers") == {
        "type": "required_reviewers",
        "prevent_self_review": True,
        "reviewers": [
            {
                "type": "User",
                "reviewer": {"id": 1001, "login": "reviewer-a", "type": "User"},
            }
        ],
    }
    assert env["deployment_branch_policy"] == CUSTOM
    bare = get_env(gh, "staging-images")
    assert bare["protection_rules"] == []
    assert bare["deployment_branch_policy"] is None
    assert bare["can_admins_bypass"] is True

    missing = gh.call("api", f"repos/{REPO}/environments/nope")
    assert missing.returncode == 1
    assert json.loads(missing.stdout)["message"] == "Not Found"
    assert missing.stderr.strip() == NOT_FOUND
    repo = json.loads(gh.call("api", f"repos/{REPO}").stdout)
    assert repo["id"] == 42
    assert repo["permissions"]["admin"] is True
    other = gh.call("api", "repos/other-owner/other/environments/production")
    assert other.returncode == 1
    assert other.stderr.strip() == NOT_FOUND


# --- branch policies --------------------------------------------------------------------------


def test_policy_list_of_an_environment_with_no_custom_policies_is_a_404(gh: Gh) -> None:
    gh.add_environment("staging-images")
    listing = f"repos/{REPO}/environments/staging-images/deployment-branch-policies"
    result = gh.call("api", "--paginate", f"{listing}?per_page=100")
    assert result.returncode == 1
    assert result.stderr.strip() == NOT_FOUND
    assert json.loads(result.stdout)["message"] == "Not Found"
    post = gh.call(
        "api", "--method", "POST", listing, "--input", "-",
        stdin=json.dumps({"name": "main", "type": "branch"}),
    )  # fmt: skip
    assert post.returncode == 1
    assert post.stderr.strip() == NOT_FOUND
    assert stored(gh, "staging-images")["policies"] == []
    # Unknown environments answer the same.
    unknown = gh.call("api", f"repos/{REPO}/environments/nope/deployment-branch-policies")
    assert unknown.returncode == 1
    assert unknown.stderr.strip() == NOT_FOUND
    # Once a PUT turns custom policies on, the list is an empty list.
    assert put(gh, "staging-images", {"deployment_branch_policy": CUSTOM}).returncode == 0
    result = gh.call("api", listing)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"total_count": 0, "branch_policies": []}


def test_branch_policies_are_a_separate_post_and_a_duplicate_is_refused(gh: Gh) -> None:
    listing = f"repos/{REPO}/environments/production/deployment-branch-policies"
    assert put(gh, "production", {"deployment_branch_policy": CUSTOM}).returncode == 0
    assert stored(gh, "production")["policies"] == []  # the PUT never creates one
    created = gh.call(
        "api", "--method", "POST", listing, "--input", "-", "--jq", ".name",
        stdin=json.dumps({"name": "main", "type": "branch"}),
    )  # fmt: skip
    assert created.returncode == 0, created.stderr
    assert created.stdout.strip() == "main"
    again = gh.call(
        "api", "--method", "POST", listing, "--input", "-",
        stdin=json.dumps({"name": "main", "type": "branch"}),
    )  # fmt: skip
    assert again.returncode == 1
    assert again.stderr.strip() == "gh: HTTP 303"
    assert [item["name"] for item in stored(gh, "production")["policies"]] == ["main"]
    other = gh.call(
        "api", "--method", "POST", listing, "--input", "-",
        stdin=json.dumps({"name": "release/*", "type": "branch"}),
    )  # fmt: skip
    assert other.returncode == 0
    listed = json.loads(gh.call("api", listing).stdout)
    assert listed["total_count"] == 2
    assert [(p["name"], p["type"]) for p in listed["branch_policies"]] == [
        ("main", "branch"),
        ("release/*", "branch"),
    ]
    extra_id = listed["branch_policies"][1]["id"]
    removed = gh.call("api", "--method", "DELETE", f"{listing}/{extra_id}")
    assert removed.returncode == 0
    assert removed.stdout == ""
    assert [p["name"] for p in json.loads(gh.call("api", listing).stdout)["branch_policies"]] == [
        "main"
    ]
    gone = gh.call("api", "--method", "DELETE", f"{listing}/{extra_id}")
    assert gone.returncode == 1
    assert gone.stderr.strip() == NOT_FOUND
    bad = gh.call("api", "--method", "POST", listing, "--input", "-", stdin=json.dumps({}))
    assert bad.returncode == 1
    assert "(HTTP 422)" in bad.stderr


def test_put_validation_rejects_a_bad_body_without_changing_anything(gh: Gh) -> None:
    assert put(gh, "production", FULL_BODY).returncode == 0
    before = gh.state()
    bad_bodies: list[Any] = [
        {"wait_timer": -1},
        {"wait_timer": "5"},
        {"prevent_self_review": "yes"},
        {"can_admins_bypass": 1},
        {"reviewers": [{"type": "Robot", "id": 1}]},
        {"reviewers": [{"type": "User", "id": "x"}]},
        {"reviewers": "reviewer-a"},
        {"deployment_branch_policy": {"protected_branches": True, "custom_branch_policies": True}},
        {"deployment_branch_policy": {"custom_branch_policies": True}},
        [],
    ]
    for body in bad_bodies:
        result = put(gh, "production", body)
        assert result.returncode == 1, body
        assert "(HTTP 422)" in result.stderr, body
    assert gh.state()["environments"] == before["environments"]
    assert put(gh, "never-made", {"wait_timer": -1}).returncode == 1
    assert "never-made" not in gh.state()["environments"]
    broken = gh.call(
        "api", "--method", "PUT", f"repos/{REPO}/environments/production", "--input", "-",
        stdin="{not json",
    )  # fmt: skip
    assert broken.returncode == 1


# --- secrets, variables, users ----------------------------------------------------------------


def test_secret_set_reads_stdin_needs_the_environment_and_refuses_body(gh: Gh) -> None:
    gh.add_environment("production")
    ok = gh.call(
        "secret", "set", "PROD_INSTANCE_ID", "--env", "production", "--repo", REPO,
        stdin="value-one",
    )  # fmt: skip
    assert ok.returncode == 0, ok.stderr
    assert stored(gh, "production")["secrets"] == {"PROD_INSTANCE_ID": "value-one"}
    again = gh.call(
        "secret", "set", "PROD_INSTANCE_ID", "--env", "production", "--repo", REPO,
        stdin="value-two",
    )  # fmt: skip
    assert again.returncode == 0
    assert stored(gh, "production")["secrets"] == {"PROD_INSTANCE_ID": "value-two"}

    missing = gh.call("secret", "set", "NAME", "--env", "nope", "--repo", REPO, stdin="v")
    assert missing.returncode == 1
    assert missing.stderr.strip() == NOT_FOUND

    for flag in ("--body", "-b"):
        before = gh.state()
        bad = gh.call(
            "secret", "set", "NAME", "--env", "production", "--repo", REPO, flag, "leaked",
            stdin="v",
        )  # fmt: skip
        assert bad.returncode == 252
        assert "fake gh:" in bad.stderr
        assert gh.state() == before
    no_repo = gh.call("secret", "set", "NAME", "--env", "production", stdin="v")
    assert no_repo.returncode == 252


def test_secret_names_are_listed_without_values_on_the_documented_path(gh: Gh) -> None:
    gh.add_environment("production", secrets={"PROD_INSTANCE_ID": "VALUE-MARKER", "B_NAME": "x"})
    path = f"repos/{REPO}/environments/production/secrets"
    raw = gh.call("api", "--paginate", f"{path}?per_page=100")
    assert raw.returncode == 0
    body = json.loads(raw.stdout)
    assert body["total_count"] == 2
    assert sorted(item["name"] for item in body["secrets"]) == ["B_NAME", "PROD_INSTANCE_ID"]
    assert "VALUE-MARKER" not in raw.stdout
    assert all(set(item) <= {"name", "created_at", "updated_at"} for item in body["secrets"])
    names = gh.call("api", "--paginate", f"{path}?per_page=100", "--jq", ".secrets[].name")
    assert names.stdout.split() == ["PROD_INSTANCE_ID", "B_NAME"] or sorted(
        names.stdout.split()
    ) == ["B_NAME", "PROD_INSTANCE_ID"]
    missing = gh.call("api", f"repos/{REPO}/environments/nope/secrets")
    assert missing.returncode == 1
    assert missing.stderr.strip() == NOT_FOUND
    legacy = gh.call("api", "repositories/42/environments/production/secrets")
    assert legacy.returncode == 252
    assert "unsupported call" in legacy.stderr


def test_variables_list_and_set(gh: Gh) -> None:
    listed = gh.call("api", "--paginate", f"repos/{REPO}/actions/variables?per_page=30")
    assert json.loads(listed.stdout) == {"total_count": 0, "variables": []}
    assert (
        gh.call(
            "variable", "set", "IMAGE_NAMESPACE", "--repo", REPO, "--body", "ghcr.io/x"
        ).returncode
        == 0
    )
    assert (
        gh.call("variable", "set", "PROD_AUTO_PLAN", "--repo", REPO, "--body", "true").returncode
        == 0
    )
    assert gh.state()["variables"] == {"IMAGE_NAMESPACE": "ghcr.io/x", "PROD_AUTO_PLAN": "true"}
    assert (
        gh.call("variable", "set", "PROD_AUTO_PLAN", "--repo", REPO, "--body", "false").returncode
        == 0
    )
    values = gh.call(
        "api", f"repos/{REPO}/actions/variables",
        "--jq", ".variables[] | [.name, .value] | @tsv",
    )  # fmt: skip
    assert sorted(values.stdout.splitlines()) == [
        "IMAGE_NAMESPACE\tghcr.io/x",
        "PROD_AUTO_PLAN\tfalse",
    ]
    wrong_repo = gh.call("variable", "set", "X", "--repo", "other-owner/other", "--body", "1")
    assert wrong_repo.returncode == 1
    assert wrong_repo.stderr.strip() == NOT_FOUND
    no_body = gh.call("variable", "set", "X", "--repo", REPO)
    assert no_body.returncode == 252
    bad_name = gh.call("variable", "set", "1BAD", "--repo", REPO, "--body", "1")
    assert bad_name.returncode == 1
    assert "(HTTP 422)" in bad_name.stderr
    assert "1BAD" not in gh.state()["variables"]


def test_users_lookup_and_unknown_user_404(gh: Gh) -> None:
    found = gh.call("api", "users/reviewer-b", "--jq", "{login, id, type}")
    assert found.returncode == 0
    assert json.loads(found.stdout) == {"login": "reviewer-b", "id": 1002, "type": "User"}
    unknown = gh.call("api", "users/nobody-here")
    assert unknown.returncode == 1
    assert unknown.stderr.strip() == NOT_FOUND
    # A user entry may be an object, so a test can plant a non-User account.
    state = gh.state()
    state["users"]["example-org"] = {"id": 7, "type": "Organization"}
    gh.save(state)
    org = json.loads(gh.call("api", "users/example-org").stdout)
    assert (org["id"], org["type"]) == (7, "Organization")


# --- the log, unsupported calls ---------------------------------------------------------------


def test_the_log_holds_argv_and_stdin_length_but_never_stdin_content(gh: Gh) -> None:
    gh.add_environment("production")
    marker = "STDIN-CONTENT-MARKER"
    gh.call(
        "secret", "set", "PROD_INSTANCE_ID", "--env", "production", "--repo", REPO, stdin=marker
    )
    gh.call(
        "api", "--method", "PUT", f"repos/{REPO}/environments/production", "--input", "-",
        stdin=json.dumps({"wait_timer": 2, "note": marker}),
    )  # fmt: skip
    gh.call("api", f"repos/{REPO}")
    log_text = gh.log_path.read_text()
    assert marker not in log_text
    calls = gh.calls()
    assert calls[0]["argv"] == [
        "secret", "set", "PROD_INSTANCE_ID", "--env", "production", "--repo", REPO
    ]  # fmt: skip
    assert calls[0]["stdin"] == len(marker)
    assert calls[1]["stdin"] == len(json.dumps({"wait_timer": 2, "note": marker}))
    assert calls[2]["stdin"] is None
    assert stored(gh, "production")["secrets"]["PROD_INSTANCE_ID"] == marker
    assert gh.commands() == [
        "secret set",
        f"api PUT repos/{REPO}/environments/production",
        f"api GET repos/{REPO}",
    ]
    gh.reset_log()
    assert gh.calls() == []


def test_writes_lists_only_calls_that_are_not_reads(gh: Gh) -> None:
    gh.add_environment("production")
    reads = [
        ("api", f"repos/{REPO}"),
        ("api", "--paginate", f"repos/{REPO}/actions/variables?per_page=30"),
        ("api", "--method", "GET", f"repos/{REPO}/environments/production"),
        ("api", "--method=GET", f"repos/{REPO}/environments/production"),
    ]
    for args in reads:
        assert gh.call(*args).returncode == 0
    assert gh.writes() == []
    gh.call(
        "api",
        "--method",
        "PUT",
        f"repos/{REPO}/environments/production",
        "--input",
        "-",
        stdin="{}",
    )
    gh.call(
        "api", "-X", "DELETE", f"repos/{REPO}/environments/production/deployment-branch-policies/1"
    )
    gh.call(
        "api",
        f"repos/{REPO}/environments/production/deployment-branch-policies",
        "--input",
        "-",
        stdin="{}",
    )
    gh.call("secret", "set", "X", "--env", "production", "--repo", REPO, stdin="v")
    gh.call("variable", "set", "X", "--repo", REPO, "--body", "1")
    gh.call("workflow", "run", "deploy-production.yml", "--ref", "main")
    gh.call("run", "cancel", "5")
    kinds = [call["argv"][:2] for call in gh.writes()]
    assert kinds == [
        ["api", "--method"],
        ["api", "-X"],
        ["api", f"repos/{REPO}/environments/production/deployment-branch-policies"],
        ["secret", "set"],
        ["variable", "set"],
        ["workflow", "run"],
        ["run", "cancel"],
    ]


def test_an_unsupported_call_is_a_bug_not_a_silent_success(gh: Gh) -> None:
    for args in (
        ("auth", "status"),
        ("api", f"repos/{REPO}/pulls"),
        ("api", "--method", "PATCH", f"repos/{REPO}/environments/production"),
        ("api", "--unknown-flag", f"repos/{REPO}"),
        ("repo", "view"),
        (),
    ):
        result = gh.call(*args)
        assert result.returncode == 252, args
        assert result.stderr.startswith("fake gh: unsupported call"), (args, result.stderr)
        assert result.stdout == ""
    assert gh.call("auth", "status").stderr.strip() == "fake gh: unsupported call: auth status"
    # Unsupported calls are still logged.
    assert gh.commands()[0] == "auth status"

    state = gh.state()
    state["logged_out"] = True
    gh.save(state)
    for args in (("api", f"repos/{REPO}"), ("api", "users/reviewer-a")):
        result = gh.call(*args)
        assert result.returncode == 4
        assert "gh auth login" in result.stderr
        assert result.stdout == ""


def test_run_executes_a_script_with_the_fake_first_on_path_and_no_tokens(
    gh: Gh, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "TOKEN-MARKER")
    monkeypatch.setenv("GITHUB_TOKEN", "TOKEN-MARKER")
    script = tmp_path / "probe.sh"
    script.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'echo "token=${GH_TOKEN:-unset}/${GITHUB_TOKEN:-unset} dir=$(pwd) tmp=$TMPDIR"\n'
        'echo "args=$*"\n'
        "IFS= read -r line || true\n"
        'echo "stdin=$line"\n'
        'gh api "repos/$1" --jq ".full_name"\n'
    )
    script.chmod(0o755)
    result = gh.run(script, REPO, stdin="from-test\n")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"token=unset/unset dir={gh.tmp} tmp={gh.tmp}"
    assert lines[1] == f"args={REPO}"
    assert lines[2] == "stdin=from-test"
    assert lines[3] == REPO
    # With no stdin given the script's stdin is empty, not the test runner's.
    quiet = gh.run(script, REPO)
    assert quiet.stdout.splitlines()[2] == "stdin="
    assert gh.run(script, REPO, env={"GH_TOKEN": "x"}).returncode == 0
