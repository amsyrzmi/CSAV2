"""The staged gate: staging records each rollout; a gated target refuses unrecorded commits."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from scripts import staging_rollout as sr
from scripts.rollout_targets import STAGING
from scripts.staging_rollout import Completed, RolloutError

from tests.test_rollout_production import HOST_TEXT, PROD, HostedRig
from tests.test_staging_rollout import (
    PREV_SHA,
    SHA,
    Answer,
    ApplyRig,
    FakeRunner,
    Predicate,
    fail,
    ok,
    step_label,
)

GATED = dataclasses.replace(PROD, staged_gate=True)
DEPLOYMENTS = "repos/amsyrzmi/CSAV2/deployments"
LISTING = f"{DEPLOYMENTS}?sha={SHA}&environment=staging&per_page=100"
REFUSAL = "commit a1b2c3d has not been rolled out to staging"
STAGED_AT = "2026-11-09T10:00:00Z"
NOT_WRITTEN = "WARNING: staging is fine but the staged record was not written: "
RETRY = f"Retry: .venv/bin/python -m scripts.staging_rollout record-staged --sha {SHA}"
DEPLOYMENT_BODY = {
    "ref": SHA,
    "environment": "staging",
    "auto_merge": False,
    "required_contexts": [],
    "transient_environment": False,
    "production_environment": False,
    "description": "staging rollout",
}
STATUS_BODY = {
    "state": "success",
    "environment": "staging",
    "auto_inactive": False,
    "description": "staging rollout finished",
}


class FakeDeployments:
    """A GitHub deployments API that filters by ``sha`` and ``environment`` like the real one."""

    def __init__(self, *deployments: dict[str, Any]) -> None:
        self.deployments = list(deployments)

    def listing(self, argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        query = parse_qs(urlsplit(argv[-1]).query)
        found = [
            {key: value for key, value in d.items() if key != "statuses"}
            for d in self.deployments
            if d["sha"] == query["sha"][0] and d["environment"] == query["environment"][0]
        ]
        return ok(json.dumps(found))

    def statuses(self, argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        number = int(urlsplit(argv[-1]).path.split("/")[-2])
        match = next(d for d in self.deployments if d["id"] == number)
        return ok(json.dumps(match["statuses"]))

    def rules(self) -> list[tuple[Predicate, Answer]]:
        return [
            (is_listing, self.listing),
            (is_statuses, self.statuses),
        ]


def is_listing(argv: Sequence[str]) -> bool:
    return argv[:2] == ["gh", "api"] and argv[2].startswith(f"{DEPLOYMENTS}?")


def is_statuses(argv: Sequence[str]) -> bool:
    return argv[:2] == ["gh", "api"] and argv[2].endswith("/statuses?per_page=100")


def is_post(argv: Sequence[str], suffix: str) -> bool:
    return argv[:4] == ["gh", "api", "--method", "POST"] and argv[4].endswith(suffix)


def deployment(
    number: int, sha: str, *states: str, created: str = STAGED_AT, environment: str = "staging"
) -> dict[str, Any]:
    statuses = [
        {"id": number * 10 + i, "state": s, "created_at": created} for i, s in enumerate(states)
    ]
    return {
        "id": number,
        "sha": sha,
        "environment": environment,
        "created_at": created,
        "statuses": statuses,
    }


def gate_runner(*deployments: dict[str, Any]) -> FakeRunner:
    return FakeRunner(FakeDeployments(*deployments).rules())


def refusal(runner: FakeRunner, sha: str = SHA) -> RolloutError:
    with pytest.raises(RolloutError) as caught:
        sr.require_staged(runner, GATED, sha)
    assert caught.value.step == "gate"
    return caught.value


# --- reading the record ------------------------------------------------------------------------


def test_require_staged_passes_with_a_success_status() -> None:
    runner = gate_runner(deployment(11, SHA, "success"))
    assert sr.require_staged(runner, GATED, SHA) == STAGED_AT
    assert runner.argvs == [
        ["gh", "api", LISTING],
        ["gh", "api", f"{DEPLOYMENTS}/11/statuses?per_page=100"],
    ]
    assert all(env is None and input is None for _, env, input in runner.calls)


def test_earliest_success_is_the_record_date() -> None:
    runner = gate_runner(
        deployment(12, SHA, "success", created="2026-11-09T12:00:00Z"),
        deployment(11, SHA, "in_progress", "success", created="2026-11-08T09:30:00Z"),
    )
    assert sr.staged_record(runner, GATED, SHA) == "2026-11-08T09:30:00Z"


def test_no_deployments_refuses_with_the_exact_message() -> None:
    error = refusal(gate_runner())
    assert error.message == REFUSAL
    assert sr.staged_record(gate_runner(), GATED, SHA) is None


def test_failure_only_statuses_refuse() -> None:
    runner = gate_runner(deployment(11, SHA, "failure", "error", "inactive", "pending"))
    assert refusal(runner).message == REFUSAL
    assert refusal(gate_runner(deployment(11, SHA))).message == REFUSAL


def test_older_success_still_counts_after_a_newer_deployment() -> None:
    api = (deployment(11, PREV_SHA, "success"), deployment(12, SHA, "inactive"))
    assert sr.require_staged(gate_runner(*api), GATED, PREV_SHA) == STAGED_AT
    assert refusal(gate_runner(*api)).message == REFUSAL


def test_a_production_deployment_is_not_a_staging_record() -> None:
    runner = gate_runner(deployment(11, SHA, "success", environment="production"))
    assert refusal(runner).message == REFUSAL


MALFORMED = [fail(1, "HTTP 502: Bad Gateway\nmore"), ok("not json"), ok("{}"), ok('["a", "b"]')]


@pytest.mark.parametrize("answer", MALFORMED, ids=["gh-error", "not-json", "object", "strings"])
@pytest.mark.parametrize("where", ["listing", "statuses"])
def test_gh_error_malformed_json_and_wrong_shape_all_refuse(where: str, answer: Completed) -> None:
    runner = gate_runner(deployment(11, SHA, "success"))
    runner.rules.insert(0, (is_listing if where == "listing" else is_statuses, answer))
    error = refusal(runner)
    assert error.message != REFUSAL
    if answer.returncode:
        assert error.message == "gh failed: HTTP 502: Bad Gateway"


@pytest.mark.parametrize(
    "shape",
    [
        {"sha": SHA, "environment": "staging"},
        {"id": "11", "sha": SHA, "environment": "staging"},
        {"id": 11, "environment": "staging"},
        {"id": 11, "sha": SHA, "environment": "production"},
    ],
    ids=["no-id", "string-id", "no-sha", "other-environment"],
)
def test_a_listed_deployment_of_the_wrong_shape_refuses(shape: dict[str, Any]) -> None:
    runner = gate_runner(deployment(11, SHA, "success"))
    runner.rules.insert(0, (is_listing, ok(json.dumps([shape]))))
    assert refusal(runner).message != REFUSAL


def test_a_success_without_a_date_refuses() -> None:
    runner = gate_runner(deployment(11, SHA, "success"))
    runner.rules.insert(0, (is_statuses, ok(json.dumps([{"state": "success"}]))))
    assert refusal(runner).message != REFUSAL


def test_deployment_for_another_sha_is_not_counted() -> None:
    other = deployment(11, PREV_SHA, "success")
    listing = {key: value for key, value in other.items() if key != "statuses"}
    runner = FakeRunner(
        [
            (is_listing, ok(json.dumps([listing]))),
            (is_statuses, ok(json.dumps(other["statuses"]))),
        ]
    )
    error = refusal(runner)
    assert error.message == "gh returned a deployment for another commit"
    assert not any(is_statuses(argv) for argv in runner.argvs)


def test_staging_target_makes_no_gate_call() -> None:
    runner = FakeRunner([])
    assert sr.require_staged(runner, STAGING, SHA) == ""
    assert sr.require_staged(runner, PROD, SHA) == ""
    assert runner.calls == []


# --- writing the record ------------------------------------------------------------------------


def record_rules(
    created: Completed | None = None, status: Completed | None = None
) -> list[tuple[Predicate, Answer]]:
    return [
        (lambda argv: is_post(argv, "/deployments"), created or ok('{"id": 42}')),
        (lambda argv: is_post(argv, "/deployments/42/statuses"), status or ok('{"id": 7}')),
    ]


def test_record_staged_posts_deployment_then_success_with_auto_inactive_false() -> None:
    runner = FakeRunner(record_rules())
    sr.record_staged(runner, STAGING, SHA)
    assert runner.argvs == [
        ["gh", "api", "--method", "POST", DEPLOYMENTS, "--input", "-"],
        ["gh", "api", "--method", "POST", f"{DEPLOYMENTS}/42/statuses", "--input", "-"],
    ]
    bodies = [json.loads(input or "") for _, _, input in runner.calls]
    assert bodies == [DEPLOYMENT_BODY, STATUS_BODY]
    assert all(env is None for _, env, _ in runner.calls)


@pytest.mark.parametrize(
    ("created", "status", "message", "posts"),
    [
        (
            fail(1, "HTTP 403: Resource not accessible"),
            None,
            "gh failed: HTTP 403: Resource not accessible",
            1,
        ),
        (ok("not json"), None, "gh returned unreadable output", 1),
        (ok('{"message": "conflict"}'), None, "gh returned an unexpected deployment", 1),
        (None, fail(1), "gh failed: exit 1", 2),
    ],
    ids=["create-denied", "create-unreadable", "create-no-id", "status-failed"],
)
def test_record_staged_failure_is_a_gate_error(
    created: Completed | None, status: Completed | None, message: str, posts: int
) -> None:
    runner = FakeRunner(record_rules(created, status))
    with pytest.raises(RolloutError) as caught:
        sr.record_staged(runner, STAGING, SHA)
    assert (caught.value.step, caught.value.message) == ("gate", message)
    assert len(runner.calls) == posts


def test_record_staged_subcommand_and_its_refusal_for_production(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = FakeRunner(record_rules())
    assert sr.main(["record-staged", "--sha", SHA], runner=runner) == 0
    assert capsys.readouterr().out == "Recorded a1b2c3d as staged\n"
    assert [json.loads(input or "") for _, _, input in runner.calls] == [
        DEPLOYMENT_BODY,
        STATUS_BODY,
    ]

    lines: list[str] = []
    runner = FakeRunner([])
    assert sr.record_staged_command(runner, GATED, SHA, lines.append) == 1
    assert lines == ["Stopped at gate: record-staged records staging rollouts only, not production"]
    assert runner.calls == []

    lines.clear()
    assert sr.record_staged_command(runner, STAGING, "a1b2c3d", lines.append) == 1
    assert lines == ["Stopped at gate: --sha must be the full 40-character SHA"]
    assert runner.calls == []

    runner = FakeRunner(record_rules(created=fail(1, "HTTP 401: Bad credentials")))
    assert sr.main(["record-staged", "--sha", SHA], runner=runner) == 1
    assert capsys.readouterr().out == "Stopped at gate: gh failed: HTTP 401: Bad credentials\n"

    with pytest.raises(SystemExit):
        sr.main(["record-staged"], runner=FakeRunner([]))


# --- main records after a successful staging apply ----------------------------------------------


@pytest.fixture
def staging_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ApplyRig:
    checks_dir = tmp_path / "checks"
    checks_dir.mkdir()
    (checks_dir / "0485.sql").write_text("SELECT 0;\n")
    secret_dir = tmp_path / "secrets"
    monkeypatch.setattr(sr, "SECRET_DIR", secret_dir)
    monkeypatch.setattr(sr, "CHECKS_DIR", checks_dir)
    return ApplyRig(tmp_path, secret_dir, monkeypatch)


def test_main_apply_on_staging_records_after_success(
    staging_rig: ApplyRig, capsys: pytest.CaptureFixture[str]
) -> None:
    staging_rig.runner.rules[:0] = record_rules()
    assert sr.main(["apply", "--sha", SHA], runner=staging_rig.runner) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-2:] == ["Rollout of a1b2c3d finished.", "Recorded a1b2c3d as staged"]
    # The two writes are the last calls, after the final compose ps of the rollout.
    argvs = staging_rig.runner.argvs
    assert step_label(argvs[-3]) == "ps"
    assert [argv[:5] for argv in argvs[-2:]] == [
        ["gh", "api", "--method", "POST", DEPLOYMENTS],
        ["gh", "api", "--method", "POST", f"{DEPLOYMENTS}/42/statuses"],
    ]
    assert sum(argv[:4] == ["gh", "api", "--method", "POST"] for argv in argvs) == 2


def test_main_apply_failure_records_nothing(
    staging_rig: ApplyRig, capsys: pytest.CaptureFixture[str]
) -> None:
    staging_rig.runner.rules[:0] = [
        (lambda argv: step_label(argv) == "config", fail(1)),
        *record_rules(),
    ]
    assert sr.main(["apply", "--sha", SHA], runner=staging_rig.runner) == 1
    assert not any(argv[0] == "gh" and "--method" in argv for argv in staging_rig.runner.argvs)
    assert "Recorded" not in capsys.readouterr().out


def test_main_record_failure_warns_with_retry_command_and_exits_zero(
    staging_rig: ApplyRig, capsys: pytest.CaptureFixture[str]
) -> None:
    staging_rig.runner.rules[:0] = record_rules(
        created=fail(1, "HTTP 403: Resource not accessible")
    )
    assert sr.main(["apply", "--sha", SHA], runner=staging_rig.runner) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-3:] == [
        "Rollout of a1b2c3d finished.",
        f"{NOT_WRITTEN}gh failed: HTTP 403: Resource not accessible",
        RETRY,
    ]


# --- the gate inside plan and apply ------------------------------------------------------------


def gated_rig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *deployments: dict[str, Any]
) -> HostedRig:
    rig = HostedRig(tmp_path, monkeypatch, HOST_TEXT)
    rig.target = dataclasses.replace(rig.target, staged_gate=True)
    rig.runner.rules[:0] = FakeDeployments(*deployments).rules()
    return rig


def test_production_plan_checks_the_gate_before_images_and_prints_the_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = gated_rig(tmp_path, monkeypatch, deployment(11, SHA, "success"))
    plan = rig.plan()
    assert plan.staged_on == STAGED_AT
    argvs = rig.runner.argvs
    gate = argvs.index(["gh", "api", LISTING])
    images = next(i for i, argv in enumerate(argvs) if "csa-python/versions" in " ".join(argv))
    merge_base = next(i for i, argv in enumerate(argvs) if "merge-base" in argv)
    assert merge_base < gate < images
    lines = sr.format_plan(plan, target=rig.target).splitlines()
    assert lines[:2] == [f"Target SHA: {SHA}", f"Staged on: {STAGED_AT}"]

    # Neither staging nor an ungated target prints the line.
    assert not any(line.startswith("Staged on") for line in sr.format_plan(plan).splitlines())
    ungated = dataclasses.replace(rig.target, staged_gate=False)
    assert "Staged on" not in sr.format_plan(plan, target=ungated)


def test_production_plan_without_a_record_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = gated_rig(tmp_path, monkeypatch, deployment(12, SHA, "failure"))
    with pytest.raises(RolloutError) as caught:
        rig.plan()
    assert (caught.value.step, caught.value.message) == ("gate", REFUSAL)
    assert not any("versions" in " ".join(argv) for argv in rig.runner.argvs)


def test_production_apply_without_a_record_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = gated_rig(tmp_path, monkeypatch)
    digest = sr.host_settings_digest(sr.load_host_values(rig.target))
    assert rig.apply(digest) == 1
    assert rig.lines == [
        f"Stopped at gate: {REFUSAL}",
        "Nothing on production was changed.",
        sr.RERUN_HINT,
    ]
    assert rig.docker_calls() == []
    assert not any("pull" in argv or "versions" in " ".join(argv) for argv in rig.runner.argvs)
    assert not any(argv[0] == "gh" and "--method" in argv for argv in rig.runner.argvs)
