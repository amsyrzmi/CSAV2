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


@pytest.mark.parametrize(
    "form",
    [("--body", "SECRET-ARGV-MARKER"), ("-b", "SECRET-ARGV-MARKER"),
     ("--body=SECRET-ARGV-MARKER",), ("-b=SECRET-ARGV-MARKER",), ("-bSECRET-ARGV-MARKER",)],
)  # fmt: skip
def test_a_secret_passed_on_argv_is_never_logged_or_printed(gh: Gh, form: tuple[str, ...]) -> None:
    gh.add_environment("production")
    result = gh.call(
        "secret", "set", "NAME", "--env", "production", "--repo", REPO, *form, stdin="v"
    )
    assert result.returncode == 252
    assert "SECRET-ARGV-MARKER" not in gh.log_path.read_text()
    assert "SECRET-ARGV-MARKER" not in result.stdout
    assert "SECRET-ARGV-MARKER" not in result.stderr
    assert "fake gh:" in result.stderr
    # The call is still logged (as a secret set), with the rest of its argv intact.
    assert gh.commands() == ["secret set"]
    assert gh.calls()[0]["argv"][:7] == [
        "secret", "set", "NAME", "--env", "production", "--repo", REPO
    ]  # fmt: skip
    # The same value on a call that is unsupported for another reason (no --repo).
    gh.reset_log()
    other = gh.call("secret", "set", "NAME", "--env", "production", *form, stdin="v")
    assert other.returncode == 252
    assert "SECRET-ARGV-MARKER" not in gh.log_path.read_text()
    assert "SECRET-ARGV-MARKER" not in other.stderr


def test_a_variable_value_on_argv_stays_in_the_log(gh: Gh) -> None:
    # Variables are not secrets and go on --body: their argv is logged as given.
    gh.call("variable", "set", "X", "--repo", REPO, "--body", "plain-value")
    assert gh.calls()[0]["argv"][-1] == "plain-value"


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
    assert names.stdout.split() == ["B_NAME", "PROD_INSTANCE_ID"]  # sorted by name
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
    assert values.stdout.splitlines() == [  # sorted by name
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


# --- how a call is read: methods, input forms, pagination -------------------------------------


def policies_url(name: str = "production") -> str:
    return f"repos/{REPO}/environments/{name}/deployment-branch-policies"


@pytest.mark.parametrize("input_args", [("--input", "-"), ("--input=-",)])
def test_a_method_less_call_with_input_is_a_post_and_a_logged_write(
    gh: Gh, input_args: tuple[str, ...]
) -> None:
    assert put(gh, "production", {"deployment_branch_policy": CUSTOM}).returncode == 0
    gh.reset_log()
    result = gh.call(
        "api", policies_url(), *input_args, stdin=json.dumps({"name": "main", "type": "branch"})
    )
    assert result.returncode == 0, result.stderr
    assert [p["name"] for p in stored(gh, "production")["policies"]] == ["main"]
    assert gh.commands() == [f"api POST {policies_url()}"]
    assert [call["argv"] for call in gh.writes()] == [["api", policies_url(), *input_args]]


def test_an_explicit_method_wins_over_the_input_default(gh: Gh) -> None:
    assert put(gh, "production", {"deployment_branch_policy": CUSTOM}).returncode == 0
    gh.reset_log()
    # --input with an explicit GET is still a GET: the list answers, nothing is created.
    result = gh.call("api", "--method", "GET", policies_url(), "--input=-", stdin="{}")
    assert result.returncode == 0
    assert gh.commands() == [f"api GET {policies_url()}"]
    assert gh.writes() == []
    assert stored(gh, "production")["policies"] == []


@pytest.mark.parametrize(
    "method_args", [("--method", "POST"), ("--method=PUT",), ("-X", "DELETE"), ("-X", "put")]
)
def test_paginate_with_a_non_get_method_is_refused_like_gh_does(
    gh: Gh, method_args: tuple[str, ...]
) -> None:
    assert put(gh, "production", {"deployment_branch_policy": CUSTOM}).returncode == 0
    before = gh.state()
    result = gh.call(
        "api", "--paginate", *method_args, policies_url(), "--input", "-",
        stdin=json.dumps({"name": "main", "type": "branch"}),
    )  # fmt: skip
    assert result.returncode == 1
    assert "the `--paginate` option is not supported for non-GET requests" in result.stderr
    assert result.stdout == ""
    assert gh.state() == before
    # The same flags in the other order refuse too.
    other = gh.call("api", policies_url(), "--method=POST", "--input=-", "--paginate", stdin="{}")
    assert other.returncode == 1
    assert "not supported for non-GET requests" in other.stderr
    assert gh.state() == before
    ok = gh.call("api", "--paginate", "--method", "GET", policies_url())
    assert ok.returncode == 0


@pytest.mark.parametrize(
    "args",
    [
        ("--paginate", "--input", "-"),
        ("--input=-", "--paginate"),
        ("--paginate", "--method", "GET", "--input", "-"),
        ("--paginate", "-X", "get", "--input=-"),
    ],
)
def test_paginate_with_input_is_refused_with_its_own_message_before_any_request(
    gh: Gh, args: tuple[str, ...]
) -> None:
    # Real gh refuses --paginate with --input (even with an explicit GET), and does so before it
    # applies the implicit POST of --input, so the message is the --input one.
    assert put(gh, "production", {"deployment_branch_policy": CUSTOM}).returncode == 0
    before = gh.state()
    result = gh.call("api", *args, policies_url(), stdin=json.dumps({"name": "main"}))
    assert result.returncode == 1, args
    assert "the `--paginate` option is not supported with `--input`" in result.stderr
    assert "non-GET" not in result.stderr
    assert result.stdout == ""
    assert gh.state() == before


@pytest.mark.parametrize(
    "field_args",
    [
        ("-f", "a=b"),
        ("-F", "a=b"),
        ("--field", "a=b"),
        ("--raw-field", "a=b"),
        ("--field=a=b",),
        ("--raw-field=a=b",),
        ("-f=a=b",),
        ("-Fa=b",),
    ],
)
@pytest.mark.parametrize("field_first", [True, False])
def test_a_field_flag_without_a_method_makes_the_call_a_post_and_a_logged_write(
    gh: Gh, field_args: tuple[str, ...], field_first: bool
) -> None:
    # Real gh turns -f, -F, --field and --raw-field into a POST when there is no --method. The
    # fake does not model fields (the call exits 252), but it is still classified as a write.
    url = f"repos/{REPO}/actions/variables"
    args = (*field_args, url) if field_first else (url, *field_args)
    result = gh.call("api", *args)
    assert result.returncode == 252
    assert gh.commands() == [f"api POST {url}"]
    assert [call["argv"] for call in gh.writes()] == [["api", *args]]
    # An explicit method wins, as with --input.
    gh.reset_log()
    gh.call("api", "--method", "GET", *args)
    assert gh.commands() == [f"api GET {url}"]
    assert gh.writes() == []


# --- the recorded bodies ----------------------------------------------------------------------


def test_api_bodies_holds_only_accepted_puts_and_policy_posts_in_order(gh: Gh) -> None:
    assert gh.api_bodies() == []
    first = {"wait_timer": 3}
    assert put(gh, "production", first).returncode == 0
    assert put(gh, "production", {"wait_timer": -1}).returncode == 1  # a 422: not recorded
    second = {"deployment_branch_policy": CUSTOM}
    assert put(gh, "production", second).returncode == 0
    post = {"name": "main", "type": "branch"}
    assert gh.call("api", policies_url(), "--input=-", stdin=json.dumps(post)).returncode == 0
    # A duplicate (303), an invalid body (422), a missing environment (404) and a DELETE: none.
    assert gh.call("api", policies_url(), "--input=-", stdin=json.dumps(post)).returncode == 1
    assert gh.call("api", policies_url(), "--input=-", stdin="{}").returncode == 1
    assert gh.call("api", policies_url("nope"), "--input=-", stdin=json.dumps(post)).returncode == 1
    assert put(gh, "production", {"reviewers": [{"type": "User", "id": 9999}]}).returncode == 1
    assert gh.api_bodies() == [
        {"method": "PUT", "path": "production", "body": first},
        {"method": "PUT", "path": "production", "body": second},
        {"method": "POST", "path": "production/policies", "body": post},
    ]


def test_api_bodies_paths_use_the_stored_spelling_of_the_environment(gh: Gh) -> None:
    assert put(gh, "Production", {"wait_timer": 3}).returncode == 0  # created as "Production"
    assert gh.api_bodies()[-1]["path"] == "Production"
    assert put(gh, "PRODUCTION", {"deployment_branch_policy": CUSTOM}).returncode == 0
    assert gh.api_bodies()[-1]["path"] == "Production"
    post = {"name": "main", "type": "branch"}
    created = gh.call("api", policies_url("PRODUCTION"), "--input=-", stdin=json.dumps(post))
    assert created.returncode == 0, created.stderr
    assert gh.api_bodies()[-1]["path"] == "Production/policies"
    # A lower-case environment asked for in another case stays lower case.
    assert put(gh, "staging", {"wait_timer": 1}).returncode == 0
    assert put(gh, "STAGING", {"wait_timer": 2}).returncode == 0
    assert gh.api_bodies()[-1]["path"] == "staging"
    assert put(gh, "STAGING", {"deployment_branch_policy": CUSTOM}).returncode == 0
    other = gh.call("api", policies_url("Staging"), "--input=-", stdin=json.dumps(post))
    assert other.returncode == 0, other.stderr
    assert gh.api_bodies()[-1]["path"] == "staging/policies"


# --- validation boundaries --------------------------------------------------------------------


def test_the_two_branch_policy_flags_must_differ(gh: Gh) -> None:
    for protected, custom in ((False, False), (True, True)):
        body = {
            "deployment_branch_policy": {
                "protected_branches": protected,
                "custom_branch_policies": custom,
            }
        }
        result = put(gh, "production", body)
        assert result.returncode == 1, body
        assert "(HTTP 422)" in result.stderr
    for protected, custom in ((True, False), (False, True)):
        body = {
            "deployment_branch_policy": {
                "protected_branches": protected,
                "custom_branch_policies": custom,
            }
        }
        assert put(gh, "production", body).returncode == 0


def test_wait_timer_is_accepted_up_to_43200_minutes(gh: Gh) -> None:
    assert put(gh, "production", {"wait_timer": 43200}).returncode == 0
    assert rule(get_env(gh, "production"), "wait_timer") == {
        "type": "wait_timer",
        "wait_timer": 43200,
    }
    refused = put(gh, "production", {"wait_timer": 43201})
    assert refused.returncode == 1
    assert "(HTTP 422)" in refused.stderr
    assert stored(gh, "production")["wait_timer"] == 43200
    assert put(gh, "production", {"wait_timer": 0}).returncode == 0


def add_users(gh: Gh, users: dict[str, Any]) -> None:
    state = gh.state()
    state["users"].update(users)
    gh.save(state)


def test_reviewers_are_capped_at_six_and_must_be_real_user_accounts(gh: Gh) -> None:
    add_users(gh, {f"user-{n}": 2000 + n for n in range(1, 8)})
    users = [{"type": "User", "id": 2000 + n} for n in range(1, 8)]
    assert put(gh, "production", {"reviewers": users[:6]}).returncode == 0
    assert len(reviewer_ids(get_env(gh, "production"))) == 6
    before = gh.state()
    too_many = put(gh, "production", {"reviewers": users})
    assert too_many.returncode == 1
    assert "(HTTP 422)" in too_many.stderr
    assert gh.state() == before
    # An id that is no user at all, and an Organization account, are refused as a User reviewer.
    add_users(gh, {"example-org": {"id": 7, "type": "Organization"}})
    for reviewers in (
        [{"type": "User", "id": 31337}],
        [{"type": "User", "id": 7}],
        [{"type": "User", "id": 1001}, {"type": "User", "id": 7}],
    ):
        result = put(gh, "production", {"reviewers": reviewers})
        assert result.returncode == 1, reviewers
        assert "(HTTP 422)" in result.stderr
    assert gh.state()["environments"] == before["environments"]
    # An empty list clears them; a Team id is not looked up (Team reviewers are out of scope).
    assert put(gh, "production", {"reviewers": []}).returncode == 0
    assert reviewer_ids(get_env(gh, "production")) == []
    assert put(gh, "production", {"reviewers": [{"type": "Team", "id": 31337}]}).returncode == 0


def test_the_same_id_with_a_different_type_is_a_different_reviewer(gh: Gh) -> None:
    both = [{"type": "User", "id": 1001}, {"type": "Team", "id": 1001}]
    assert put(gh, "production", {"reviewers": both}).returncode == 0
    assert stored(gh, "production")["reviewers"] == both
    env = get_env(gh, "production")
    found = rule(env, "required_reviewers")
    assert found is not None
    assert [item["type"] for item in found["reviewers"]] == ["User", "Team"]


# --- jq ---------------------------------------------------------------------------------------


def test_jq_output_has_sorted_object_keys_like_gh(gh: Gh) -> None:
    result = gh.call("api", f"repos/{REPO}", "--jq", "{z: .id, a: .id, m: .full_name}")
    assert result.returncode == 0
    assert result.stdout.strip() == '{"a":42,"m":"example-owner/CSAV2","z":42}'
    plain = gh.call("api", "users/reviewer-a", "--jq", "{type, login, id}")
    assert plain.stdout.strip() == '{"id":1001,"login":"reviewer-a","type":"User"}'


# --- gh secret set / gh variable set ----------------------------------------------------------


def test_a_secret_from_stdin_loses_only_its_trailing_newlines(gh: Gh) -> None:
    gh.add_environment("production")
    for sent, kept in (
        ("value\n", "value"),
        ("value\r\n\r\n", "value"),
        ("  two words  \n", "  two words  "),
        ("line1\nline2\n", "line1\nline2"),
    ):
        done = gh.call("secret", "set", "S", "--env", "production", "--repo", REPO, stdin=sent)
        assert done.returncode == 0
        assert stored(gh, "production")["secrets"] == {"S": kept}


def test_secret_and_variable_names_are_stored_uppercase_and_github_prefix_is_reserved(
    gh: Gh,
) -> None:
    gh.add_environment("production")
    done = gh.call("secret", "set", "prod_id", "--env", "production", "--repo", REPO, stdin="v")
    assert done.returncode == 0
    assert list(stored(gh, "production")["secrets"]) == ["PROD_ID"]
    done = gh.call("secret", "set", "PROD_ID", "--env", "production", "--repo", REPO, stdin="w")
    assert stored(gh, "production")["secrets"] == {"PROD_ID": "w"}  # the same secret
    assert gh.call("variable", "set", "my_var", "--repo", REPO, "--body", "1").returncode == 0
    assert gh.state()["variables"] == {"MY_VAR": "1"}
    for name in ("GITHUB_X", "github_x", "Github_Y"):
        secret = gh.call("secret", "set", name, "--env", "production", "--repo", REPO, stdin="v")
        variable = gh.call("variable", "set", name, "--repo", REPO, "--body", "1")
        for result in (secret, variable):
            assert result.returncode == 1, name
            assert "(HTTP 422)" in result.stderr
    assert list(stored(gh, "production")["secrets"]) == ["PROD_ID"]
    assert list(gh.state()["variables"]) == ["MY_VAR"]


def test_secret_and_variable_set_stop_with_exit_4_when_logged_out(gh: Gh) -> None:
    gh.add_environment("production")
    state = gh.state()
    state["logged_out"] = True
    gh.save(state)
    before = gh.state()
    secret = gh.call("secret", "set", "S", "--env", "production", "--repo", REPO, stdin="v")
    variable = gh.call("variable", "set", "V", "--repo", REPO, "--body", "1")
    for result in (secret, variable):
        assert result.returncode == 4
        assert "gh auth login" in result.stderr
        assert result.stdout == ""
    assert gh.state() == before


def test_the_logged_stdin_length_counts_bytes_not_characters(gh: Gh) -> None:
    gh.add_environment("production")
    value = "caf\u00e9-\u00fc"  # 6 characters, 8 bytes
    done = gh.call("secret", "set", "S", "--env", "production", "--repo", REPO, stdin=value)
    assert done.returncode == 0
    assert gh.calls()[-1]["stdin"] == 8
    assert stored(gh, "production")["secrets"] == {"S": value}


def test_secrets_and_variables_are_listed_sorted_by_name(gh: Gh) -> None:
    gh.add_environment("production", secrets={"ZED": "1", "ALPHA": "2", "MID": "3"})
    for name in ("ZZ", "AA", "MM"):
        gh.call("variable", "set", name, "--repo", REPO, "--body", "1")
    secrets = gh.call("api", f"repos/{REPO}/environments/production/secrets")
    assert [s["name"] for s in json.loads(secrets.stdout)["secrets"]] == ["ALPHA", "MID", "ZED"]
    variables = gh.call("api", f"repos/{REPO}/actions/variables")
    assert [v["name"] for v in json.loads(variables.stdout)["variables"]] == ["AA", "MM", "ZZ"]


# --- case-insensitive names -------------------------------------------------------------------


def test_environment_names_are_case_insensitive_and_the_stored_name_is_returned(gh: Gh) -> None:
    gh.add_environment("production")
    assert get_env(gh, "PRODUCTION")["name"] == "production"
    assert put(gh, "Production", {"wait_timer": 4}).returncode == 0
    assert list(gh.state()["environments"]) == ["production"]  # no second environment
    assert stored(gh, "production")["wait_timer"] == 4
    done = gh.call("secret", "set", "S", "--env", "PRODUCTION", "--repo", REPO, stdin="v")
    assert done.returncode == 0
    assert stored(gh, "production")["secrets"] == {"S": "v"}
    secrets = gh.call("api", f"repos/{REPO}/environments/PRODUCTION/secrets")
    assert json.loads(secrets.stdout)["total_count"] == 1
    assert put(gh, "production", {"deployment_branch_policy": CUSTOM}).returncode == 0
    created = gh.call(
        "api", policies_url("PRODUCTION"), "--input=-",
        stdin=json.dumps({"name": "main", "type": "branch"}),
    )  # fmt: skip
    assert created.returncode == 0
    assert (
        len(json.loads(gh.call("api", policies_url("Production")).stdout)["branch_policies"]) == 1
    )
    # A new environment keeps the case it was created with.
    assert put(gh, "Staging", {"wait_timer": 1}).returncode == 0
    assert get_env(gh, "staging")["name"] == "Staging"


def test_user_lookup_is_case_insensitive_and_returns_the_stored_login(gh: Gh) -> None:
    add_users(gh, {"MixedCaseUser": 3001})
    for asked in ("MixedCaseUser", "mixedcaseuser", "MIXEDCASEUSER"):
        found = gh.call("api", f"users/{asked}", "--jq", "{login, id, type}")
        assert found.returncode == 0, asked
        assert json.loads(found.stdout) == {"login": "MixedCaseUser", "id": 3001, "type": "User"}
    assert put(gh, "production", {"reviewers": [{"type": "User", "id": 3001}]}).returncode == 0
    found_rule = rule(get_env(gh, "production"), "required_reviewers")
    assert found_rule is not None
    assert found_rule["reviewers"][0]["reviewer"]["login"] == "MixedCaseUser"


def test_the_repository_name_matches_case_insensitively_and_the_stored_one_is_returned(
    gh: Gh,
) -> None:
    gh.add_environment("production")
    asked = REPO.upper()
    found = gh.call("api", f"repos/{asked}", "--jq", ".full_name")
    assert found.returncode == 0, found.stderr
    assert found.stdout.strip() == REPO
    assert gh.call("api", f"repos/{asked.lower()}/actions/variables").returncode == 0
    assert gh.call("api", f"repos/{asked}/environments/production").returncode == 0
    wrong = gh.call("api", "repos/other-owner/other")
    assert wrong.returncode == 1
    assert wrong.stderr.strip() == NOT_FOUND
    done = gh.call("secret", "set", "S", "--env", "production", "--repo", asked, stdin="v")
    assert done.returncode == 0, done.stderr
    assert stored(gh, "production")["secrets"] == {"S": "v"}
    assert gh.call("variable", "set", "V", "--repo", asked, "--body", "1").returncode == 0
    assert gh.state()["variables"] == {"V": "1"}
    other = gh.call("variable", "set", "V", "--repo", "other-owner/other", "--body", "1")
    assert other.stderr.strip() == NOT_FOUND
