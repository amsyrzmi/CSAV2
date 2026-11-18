"""Staging deploy assets: the two Systems Manager documents, the OIDC role template, and the
per-target behaviour of ``ssm_run.sh`` and ``parse_plan.sh``.

The shell scripts run for real against the stub ``aws`` of the production harness; the documents
and policies are checked as data. Stand-ins only: no account, node, region or address.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from scripts import staging_rollout as sr
from scripts.rollout_targets import STAGING

from tests.test_production_deploy_assets import (
    AUD,
    BAD_VALUES,
    COMMAND_ID,
    DEFAULTS,
    DIGEST,
    FE_DIGEST,
    HOST_VALUES,
    IMAGE_DIGEST_PATTERN,
    INSTANCE_ARN,
    MIGRATIONS,
    PARSE_PLAN,
    PY_DIGEST,
    SHA,
    SSM_ENV,
    SSM_RUN,
    SUB,
    SUBJECT_PLACEHOLDER,
    SUBJECT_PREFIX,
    Harness,
    _filled_policy,
    _outputs,
    _parse,
    _plan_output,
    _policy,
    _send_argv,
    _trust_allows,
    _unquoted_expansions,
)

ROOT = Path(__file__).resolve().parents[1]
STAGING_DIR = ROOT / "deploy" / "staging"
IAM_DIR = STAGING_DIR / "iam"
PLAN_DOCUMENT = STAGING_DIR / "ssm-document-plan.json"
APPLY_DOCUMENT = STAGING_DIR / "ssm-document-apply.json"
TRUST = IAM_DIR / "trust-policy.json"
PERMISSIONS = IAM_DIR / "permissions-policy.json"

DOCUMENTS = {"plan": PLAN_DOCUMENT, "apply": APPLY_DOCUMENT}
DOCUMENT_NAMES = {"plan": "csa-staging-plan", "apply": "csa-staging-apply"}
DOCUMENT_PATTERNS = {
    "plan": {
        "sha": "^[0-9a-f]{40}$",
        "pythonDigest": IMAGE_DIGEST_PATTERN,
        "frontendDigest": IMAGE_DIGEST_PATTERN,
    },
    "apply": {
        "sha": "^[0-9a-f]{40}$",
        "migrations": "^([0-9]{4}(,[0-9]{4})*)?$",
        "pythonDigest": IMAGE_DIGEST_PATTERN,
        "frontendDigest": IMAGE_DIGEST_PATTERN,
    },
}
ENTRY_COMMAND = "exec runuser -u csa-deploy -- /usr/local/sbin/csa-staging-deploy "
DOCUMENT_COMMANDS = {
    "plan": ENTRY_COMMAND + 'plan "{{ sha }}" "" "" "{{ pythonDigest }}" "{{ frontendDigest }}"',
    "apply": ENTRY_COMMAND
    + 'apply "{{ sha }}" "{{ migrations }}" "" "{{ pythonDigest }}" "{{ frontendDigest }}"',
}


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    """A harness for the shared runner scripts; the target is picked per call by the test."""
    return Harness(tmp_path, target="staging")


def _document(kind: str) -> dict[str, Any]:
    return json.loads(DOCUMENTS[kind].read_text())


# --- the two documents ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["plan", "apply"])
def test_staging_documents_have_the_exact_patterns_and_fix_their_action(kind: str) -> None:
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
    # The action is fixed by the document, never a parameter, and there is no host digest.
    assert not {"action", "hostDigest"} & set(parameters)

    (step,) = document["mainSteps"]
    assert step["action"] == "aws:runShellScript"
    assert step["name"] == "deploy"
    assert step["inputs"]["timeoutSeconds"] == "7200"
    commands = step["inputs"]["runCommand"]
    assert commands == ["set -eu", DOCUMENT_COMMANDS[kind]]

    command = commands[1]
    placeholders = re.findall(r"\{\{[^}]*\}\}", command)
    names = [re.fullmatch(r"\{\{ (\w+) \}\}", item) for item in placeholders]
    assert all(names)
    assert [match.group(1) for match in names if match] == list(DOCUMENT_PATTERNS[kind])
    assert len(re.findall(r'"\{\{ \w+ \}\}"', command)) == len(placeholders)
    assert "{{" not in json.dumps({k: v for k, v in document.items() if k != "mainSteps"})

    other = "apply" if kind == "plan" else "plan"
    assert f" {other} " not in command


@pytest.mark.parametrize("kind", ["plan", "apply"])
def test_staging_documents_run_the_staging_entry_as_csa_deploy_with_an_empty_host_digest(
    kind: str,
) -> None:
    command = _document(kind)["mainSteps"][0]["inputs"]["runCommand"][1]

    assert command.startswith(ENTRY_COMMAND + f"{kind} ")
    assert "csa-prod-deploy" not in command
    assert "ubuntu" not in command
    # Quoted arguments: sha, migrations, host digest (always empty), then the two image digests.
    arguments = re.findall(r'"([^"]*)"', command)
    assert arguments[2] == ""
    assert arguments[-2:] == ["{{ pythonDigest }}", "{{ frontendDigest }}"]
    if kind == "plan":
        assert arguments == ["{{ sha }}", "", "", "{{ pythonDigest }}", "{{ frontendDigest }}"]
    else:
        assert arguments == [
            "{{ sha }}",
            "{{ migrations }}",
            "",
            "{{ pythonDigest }}",
            "{{ frontendDigest }}",
        ]


@pytest.mark.parametrize("kind", ["plan", "apply"])
def test_staging_document_patterns_reject_injection(kind: str) -> None:
    parameters = _document(kind)["parameters"]

    for name in parameters:
        pattern = parameters[name]["allowedPattern"]
        assert BAD_VALUES[name]
        for value in BAD_VALUES[name]:
            assert re.fullmatch(pattern, value) is None, (name, value)
        assert re.fullmatch(pattern, DEFAULTS[name]), name
    if kind == "apply":
        assert re.fullmatch(parameters["migrations"]["allowedPattern"], "")


@pytest.mark.parametrize("kind", ["plan", "apply"])
def test_staging_document_timeout_matches_production(kind: str) -> None:
    timeout = int(_document(kind)["mainSteps"][0]["inputs"]["timeoutSeconds"])
    (runner_wait,) = re.findall(r': "\$\{SSM_MAX_SECONDS:=(\d+)\}"', SSM_RUN.read_text())

    assert timeout == 7200
    assert int(runner_wait) == 6900
    assert timeout > int(runner_wait)


# --- the role ------------------------------------------------------------------------------------

STAGING_SUB = f"{SUBJECT_PLACEHOLDER}:environment:staging"


def test_staging_trust_policy_is_scoped_to_this_repo_and_the_staging_environment() -> None:
    policy = _policy(TRUST)

    assert set(policy) == {"Version", "Statement"}
    assert policy["Version"] == "2012-10-17"
    (statement,) = policy["Statement"]
    assert set(statement) == {"Effect", "Principal", "Action", "Condition"}
    assert statement["Effect"] == "Allow"
    assert statement["Principal"] == {"Federated": "<oidc-provider-arn>"}
    assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert statement["Condition"] == {"StringEquals": {AUD: "sts.amazonaws.com", SUB: STAGING_SUB}}
    assert "*" not in json.dumps(statement["Condition"])


def test_staging_trust_admits_only_the_staging_environment() -> None:
    policy = _filled_policy(TRUST)

    def token(sub: str, aud: str = "sts.amazonaws.com") -> dict[str, str]:
        return {AUD: aud, SUB: sub}

    assert _trust_allows(policy, token(f"{SUBJECT_PREFIX}:environment:staging"))
    for sub in (
        f"{SUBJECT_PREFIX}:environment:production",
        f"{SUBJECT_PREFIX}:environment:production-plan",
        f"{SUBJECT_PREFIX}:environment:staging-extra",
        f"{SUBJECT_PREFIX}:ref:refs/heads/main",
        f"{SUBJECT_PREFIX}:pull_request",
        "repo:example-owner@1001/CSAV2@9999:environment:staging",  # another repository id
        "repo:someone@7/CSAV2@2002:environment:staging",  # another owner
        "repo:example-owner/CSAV2:environment:staging",  # the classic form, not this prefix
    ):
        assert not _trust_allows(policy, token(sub)), sub
    assert not _trust_allows(policy, token(f"{SUBJECT_PREFIX}:environment:staging", "other"))


def test_staging_permissions_name_only_the_two_staging_documents_and_one_node() -> None:
    policy = _policy(PERMISSIONS)
    arns = [
        f"arn:aws:ssm:<region>:<account-id>:document/{name}" for name in DOCUMENT_NAMES.values()
    ]

    assert set(policy) == {"Version", "Statement"}
    assert policy["Version"] == "2012-10-17"
    statements = policy["Statement"]
    for statement in statements:
        assert set(statement) <= {"Sid", "Effect", "Action", "Resource"}
        assert statement["Effect"] == "Allow"
    actions = [
        a
        for s in statements
        for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])
    ]
    assert sorted(actions) == ["ssm:GetCommandInvocation", "ssm:SendCommand"]

    (send,) = [s for s in statements if s["Action"] == "ssm:SendCommand"]
    assert sorted(send["Resource"]) == sorted([*arns, INSTANCE_ARN])
    assert "*" not in json.dumps(send)
    (read,) = [s for s in statements if s is not send]
    assert read["Action"] == "ssm:GetCommandInvocation"
    assert read["Resource"] == "*"

    text = PERMISSIONS.read_text()
    assert text.count(":document/") == 2
    assert "production" not in text


STAGING_NEW_FILES = (PLAN_DOCUMENT, APPLY_DOCUMENT, TRUST, PERMISSIONS)


@pytest.mark.parametrize("path", STAGING_NEW_FILES, ids=lambda path: path.name)
def test_staging_files_hold_no_account_node_region_or_address(path: Path) -> None:
    text = path.read_text()

    assert not re.search(r"\b\d{12}\b", text)
    assert not re.search(r"\b(?:mi|i)-[0-9a-f]{8,17}\b", text)
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)
    assert not re.search(r"\b(?:us|eu|ap|sa|ca|me|af|il|mx)-[a-z]+-\d\b", text)
    assert not re.search(r"arn:aws:iam::\d", text)
    assert not re.search(r"arn:aws:[a-z]+:[a-z0-9-]+:\d", text)


# --- ssm_run.sh, per target ----------------------------------------------------------------------


def test_ssm_run_staging_sends_the_staging_documents_without_a_host_digest(
    harness: Harness,
) -> None:
    plan = harness.ssm(["plan", SHA], DEPLOY_TARGET="staging")
    assert plan.returncode == 0, plan.stderr
    send, _poll = harness.aws_calls()
    assert send == _send_argv("csa-staging-plan", send[9])
    assert json.loads(send[9]) == {
        "sha": [SHA],
        "pythonDigest": [PY_DIGEST],
        "frontendDigest": [FE_DIGEST],
    }

    harness.log.unlink()
    apply = harness.ssm(["apply", SHA, MIGRATIONS], DEPLOY_TARGET="staging")
    assert apply.returncode == 0, apply.stderr
    send, _poll = harness.aws_calls()
    assert send == _send_argv("csa-staging-apply", send[9])
    assert json.loads(send[9]) == {
        "sha": [SHA],
        "migrations": [MIGRATIONS],
        "pythonDigest": [PY_DIGEST],
        "frontendDigest": [FE_DIGEST],
    }
    assert COMMAND_ID.encode() in apply.stderr

    harness.log.unlink()
    no_migrations = harness.ssm(["apply", SHA, "", ""], DEPLOY_TARGET="staging")
    assert no_migrations.returncode == 0, no_migrations.stderr
    send, _poll = harness.aws_calls()
    assert json.loads(send[9]) == {
        "sha": [SHA],
        "pythonDigest": [PY_DIGEST],
        "frontendDigest": [FE_DIGEST],
    }


@pytest.mark.parametrize("value", [None, "", "prod", "Staging", "staging\n", "staging;id"])
def test_ssm_run_refuses_a_missing_or_unknown_deploy_target_and_sends_nothing(
    harness: Harness, value: str | None
) -> None:
    for args in (["plan", SHA], ["apply", SHA, "", DIGEST]):
        if value is None:
            result = harness.ssm(args, unset=["DEPLOY_TARGET"])
        else:
            result = harness.ssm(args, DEPLOY_TARGET=value)

        assert result.returncode == 64
        assert b"DEPLOY_TARGET" in result.stderr
        assert harness.calls() == []


@pytest.mark.parametrize("host_digest", [DIGEST, "x", "0" * 12])
def test_ssm_run_staging_refuses_a_host_digest(harness: Harness, host_digest: str) -> None:
    result = harness.ssm(["apply", SHA, "", host_digest], DEPLOY_TARGET="staging")

    assert result.returncode == 64
    assert b"staging takes no host digest" in result.stderr
    assert harness.calls() == []


def test_ssm_run_staging_plan_refuses_migrations_and_a_host_digest(harness: Harness) -> None:
    for args in (["plan", SHA, MIGRATIONS], ["plan", SHA, "", DIGEST]):
        result = harness.ssm(args, DEPLOY_TARGET="staging")
        assert result.returncode == 64
        assert harness.calls() == []


def test_ssm_run_production_still_needs_its_host_digest(harness: Harness) -> None:
    result = harness.ssm(["apply", SHA, "", ""], DEPLOY_TARGET="production")

    assert result.returncode == 64
    assert harness.calls() == []


def test_the_shared_runner_scripts_stay_quoted_and_read_the_target() -> None:
    for script in (SSM_RUN, PARSE_PLAN):
        text = script.read_text()
        assert _unquoted_expansions(text) == []
        assert "DEPLOY_TARGET" in text
    assert SSM_ENV["DEPLOY_TARGET"] == "production"


# --- parse_plan.sh, per target -------------------------------------------------------------------


class _FakeRunner:
    """A runner whose ``git merge-base`` answers with a fixed status."""

    def __init__(self, status: int) -> None:
        self.status = status

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        input: str | None = None,  # noqa: A002
    ) -> sr.Completed:
        return sr.Completed(self.status, "", "")


def _warning_lines(status: int) -> list[str]:
    """The warning ``build_plan`` would print: status 0 is a downgrade, 2 cannot compare."""
    rollback = {
        "gateway": sr.RunningImage("ghcr.io/example/csa-python@sha256:" + "1" * 64, "f" * 40)
    }
    return sr.downgrade_warnings(_FakeRunner(status), Path("/nonexistent"), SHA, rollback)


def _staging_output(*, warnings: list[str] | None = None, missing: list[str] | None = None) -> str:
    images = {app: f"ghcr.io/example/csa-{app}@sha256:{'1' * 64}" for app in sr.APPS}
    plan = sr.Plan(
        SHA,
        images,
        {},
        [],
        missing or [],
        [],
        sr.plan_steps([], [], target=STAGING),
        {},
        host_values=HOST_VALUES,
        warnings=warnings or [],
    )
    return sr.format_plan(plan, target=STAGING) + "\n"


def test_parse_plan_staging_reads_a_plan_without_a_host_digest(harness: Harness) -> None:
    for pending, migrations in ((["0486", "0487"], "0486,0487"), ([], "")):
        result = _parse(harness, _plan_output(pending, production=False), target="staging")

        assert result.returncode == 0, result.stderr
        assert result.stdout == _outputs(SHA, migrations, "")


def test_parse_plan_staging_refuses_a_plan_for_another_commit(harness: Harness) -> None:
    result = _parse(harness, _plan_output([], sha="f" * 40, production=False), target="staging")

    assert result.returncode == 65
    assert result.stdout == b""


@pytest.mark.parametrize("target", ["staging", "production"])
@pytest.mark.parametrize(
    "extra",
    [
        pytest.param("Stopped at lock: another rollout of staging is running\n", id="stopped"),
        pytest.param("Stopped at checks: boom\n", id="stopped-other-step"),
        pytest.param("Missing secret files: gateway.env, bot.env\n", id="missing-secrets"),
    ],
)
def test_parse_plan_refuses_stopped_and_missing_secret_lines(
    harness: Harness, target: str, extra: str
) -> None:
    text = _plan_output(["0486"], production=target == "production") + extra
    result = _parse(harness, text, target=target)

    assert result.returncode == 65
    assert result.stdout == b""
    assert result.stderr.startswith(b"parse_plan: ")


@pytest.mark.parametrize("target", ["staging", "production"])
def test_parse_plan_refuses_the_real_missing_secret_output_of_a_plan(
    harness: Harness, target: str
) -> None:
    text = _staging_output(missing=["gateway.env"])
    if target == "production":
        text += f"Host settings digest: {DIGEST}\n"
    result = _parse(harness, text, target=target)

    assert result.returncode == 65
    assert result.stdout == b""
    assert b"missing secret files" in result.stderr


def test_parse_plan_staging_refuses_a_downgrade_and_production_accepts_it(
    harness: Harness,
) -> None:
    (downgrade,) = _warning_lines(0)
    assert "this is a downgrade" in downgrade

    staging = _parse(harness, _staging_output(warnings=[downgrade]), target="staging")
    assert staging.returncode == 65
    assert staging.stdout == b""
    assert b"the plan is a downgrade; staging never goes back by itself" in staging.stderr
    assert b"deploy/STAGING_PIPELINE.md" in staging.stderr

    # Production keeps accepting a downgrade: the owner's approval decides.
    text = _plan_output(["0486"])
    text = text.replace("Rollback references", downgrade + "\nRollback references", 1)
    production = _parse(harness, text, target="production")
    assert production.returncode == 0, production.stderr
    assert production.stdout.startswith(f"sha={SHA}\n".encode())


def test_parse_plan_staging_shows_other_warnings_without_refusing(harness: Harness) -> None:
    (cannot_compare,) = _warning_lines(2)
    assert "could not compare" in cannot_compare

    result = _parse(harness, _staging_output(warnings=[cannot_compare]), target="staging")

    assert result.returncode == 0, result.stderr
    assert result.stdout == _outputs(SHA, "", "")


def test_parse_plan_only_a_line_starting_with_the_marker_counts(harness: Harness) -> None:
    # A downgrade phrase or a Stopped marker inside another line is not a plan line.
    text = _staging_output() + "note: Stopped at nothing; Missing secret files: none\n"
    text += "rollout said WARNING: x this is a downgrade\n"
    result = _parse(harness, text, target="staging")

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", [None, "", "prod", "Staging", "staging\n"])
def test_parse_plan_refuses_a_missing_or_unknown_deploy_target(
    harness: Harness, value: str | None
) -> None:
    plan = harness.root / "plan.txt"
    plan.write_text(_plan_output([]))
    env = harness.env(LC_ALL="C")
    if value is not None:
        env["DEPLOY_TARGET"] = value
    result = harness.run_script(PARSE_PLAN, [str(plan), SHA], env=env)

    assert result.returncode == 65
    assert result.stdout == b""
    assert b"DEPLOY_TARGET" in result.stderr
