"""The rollout core driven by a non-staging target: names, replicas and wording."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from scripts import staging_rollout as sr
from scripts.rollout_targets import STAGING, Target
from scripts.staging_rollout import Completed, RolloutError

from tests.test_staging_rollout import (
    DIGEST_A,
    DIGEST_B,
    FLAGS_TEXT,
    FRONTEND_REF,
    PYTHON_REF,
    RUNNING_FORMAT,
    RUNNING_REF,
    SHA,
    FakeRunner,
    fail,
    has,
    ok,
    running_json,
    versions_json,
)

PROD = dataclasses.replace(
    STAGING,
    name="production",
    project="csa-prod",
    secret_dir=Path("/srv/csa-prod/secrets"),
    compose_files=(*STAGING.compose_files, "deploy/compose.production.yml"),
    replicas={"worker": ("CSA_PROD_WORKER_REPLICAS", 2)},
    db_name="csa_staging",
)
DOCKER = ["docker", "--host", sr.SOCKET]
IMAGE_ONE = "sha256:" + "1" * 64
IMAGE_TWO = "sha256:" + "2" * 64


def test_compose_base_uses_the_target_project_files_and_profiles() -> None:
    assert sr.compose_base(target=PROD) == [
        *DOCKER,
        "compose",
        "--env-file",
        "/dev/null",
        "--project-name",
        "csa-prod",
        "--file",
        "deploy/compose.staging-local-proposal.yml",
        "--file",
        "deploy/compose.staging-shared-caddy.yml",
        "--file",
        "deploy/compose.staging-pipeline.yml",
        "--file",
        "deploy/compose.production.yml",
        "--profile",
        "staging-approved",
        "--profile",
        "pipeline-approved",
    ]
    assert sr.compose_base() == sr.compose_base(target=STAGING)
    assert "csa-staging" in sr.compose_base()


def test_container_names_default_and_override() -> None:
    assert sr.container_names(PROD, {}, "worker") == ["csa-prod-worker-1", "csa-prod-worker-2"]
    assert sr.container_names(PROD, {"CSA_PROD_WORKER_REPLICAS": "1"}, "worker") == [
        "csa-prod-worker-1"
    ]
    assert sr.container_names(PROD, {"CSA_PROD_WORKER_REPLICAS": "3"}, "worker") == [
        "csa-prod-worker-1",
        "csa-prod-worker-2",
        "csa-prod-worker-3",
    ]
    for bad in ("x", "0", "-1", ""):
        with pytest.raises(RolloutError) as caught:
            sr.container_names(PROD, {"CSA_PROD_WORKER_REPLICAS": bad}, "worker")
        assert caught.value.step == "flags"
        assert "CSA_PROD_WORKER_REPLICAS" in caught.value.message
    assert sr.container_names(PROD, {"CSA_PROD_WORKER_REPLICAS": "4"}, "gateway") == [
        "csa-prod-gateway-1"
    ]
    assert sr.container_names(STAGING, {}, "worker") == ["csa-staging-worker-1"]


def test_restart_command_starts_every_replica() -> None:
    assert sr.restart_command(PROD, {}) == (
        "docker --host unix:///var/run/docker.sock start csa-prod-gateway-1 "
        "csa-prod-frontend-1 csa-prod-worker-1 csa-prod-worker-2 csa-prod-delivery-1"
    )
    assert sr.restart_command(STAGING, {}) == sr.RESTART_COMMAND


def replica_runner(images: Mapping[str, str]) -> FakeRunner:
    """A failed ``up`` for worker; every replica healthy; each on the ref in ``images``."""
    ids = {"csa-prod-worker-1": IMAGE_ONE, "csa-prod-worker-2": IMAGE_TWO}
    refs = {IMAGE_ONE: images["csa-prod-worker-1"], IMAGE_TWO: images["csa-prod-worker-2"]}

    def image_id(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        return ok(ids[argv[-1]] + "\n")

    def described(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        return ok(running_json(refs[argv[-1]]))

    return FakeRunner(
        [
            (has(" up "), fail(1)),
            (has("{{.State.Health.Status}}"), ok("healthy\n")),
            (has("inspect --format {{.Image}}"), image_id),
            (lambda argv: RUNNING_FORMAT in argv, described),
        ]
    )


def test_recreate_unhealthy_path_checks_every_replica() -> None:
    lines: list[str] = []
    sleeps: list[float] = []
    runner = replica_runner({"csa-prod-worker-1": PYTHON_REF, "csa-prod-worker-2": RUNNING_REF})
    with pytest.raises(RolloutError) as caught:
        sr._recreate(
            runner,
            {},
            "worker",
            PYTHON_REF,
            sleeps.append,
            lines.append,
            stopped=False,
            target=PROD,
        )
    assert caught.value.step == "recreate worker"
    assert "not healthy on the new image" in caught.value.message
    assert sum(sleeps) == sr.HEALTH_WAIT
    inspected = {argv[-1] for argv in runner.argvs if "{{.State.Health.Status}}" in argv}
    assert inspected == {"csa-prod-worker-1", "csa-prod-worker-2"}
    assert runner.argvs[0][6:8] == ["--project-name", "csa-prod"]

    sleeps.clear()
    runner = replica_runner({"csa-prod-worker-1": PYTHON_REF, "csa-prod-worker-2": PYTHON_REF})
    sr._recreate(
        runner, {}, "worker", PYTHON_REF, sleeps.append, lines.append, stopped=False, target=PROD
    )
    assert sleeps == []
    described = [argv[-1] for argv in runner.argvs if RUNNING_FORMAT in argv]
    assert described == [IMAGE_ONE, IMAGE_TWO]


def test_messages_name_the_target() -> None:
    runner = FakeRunner([(has("psql"), fail(1))])
    with pytest.raises(RolloutError) as caught:
        sr.applied_versions(runner, {}, target=PROD)
    assert caught.value.message == "could not read schema_migrations from production (exit 1)"
    with pytest.raises(RolloutError) as caught:
        sr.applied_versions(runner, {})
    assert caught.value.message == "could not read schema_migrations from staging (exit 1)"

    with pytest.raises(RolloutError) as caught:
        sr.pending_versions(["0484"], ["0484", "0485"], target=PROD)
    assert caught.value.message.startswith("production has migrations this checkout lacks")
    with pytest.raises(RolloutError) as caught:
        sr.pending_versions(["0484"], ["0484", "0485"])
    assert caught.value.message.startswith("staging has migrations this checkout lacks")

    lines: list[str] = []
    sr._report_recovery(sr._Progress(), lines.append, target=PROD)
    sr._report_recovery(sr._Progress(), lines.append)
    assert lines == ["Nothing on production was changed.", "Nothing on staging was changed."]


def local_image_runner() -> FakeRunner:
    """Every app container runs a local image: no ghcr.io/amsyrzmi/ digest."""
    return FakeRunner(
        [
            (has("inspect --format {{.Image}}"), ok(IMAGE_ONE + "\n")),
            (has("image inspect"), ok(running_json("docker.io/library/local@sha256:" + "d" * 64))),
        ]
    )


def local_plan(target: Target) -> sr.Plan:
    rollback = sr.running_images(local_image_runner(), {}, target=target)
    images = {"python": PYTHON_REF, "frontend": FRONTEND_REF}
    steps = sr.plan_steps([], [], target=target)
    return sr.Plan(SHA, images, rollback, [], [], [], steps, {})


def test_production_rollback_is_unknown_for_local_images() -> None:
    production = local_plan(PROD)
    assert production.rollback == {service: None for service in PROD.app_services}
    printed = sr.format_plan(production, target=PROD).splitlines()
    for service in PROD.app_services:
        assert f"  {service}: unknown (a local image with no registry digest, or not running)" in (
            printed
        )
    lines: list[str] = []
    progress = sr._Progress(phase="recreating", rollback=production.rollback)
    sr._report_recovery(progress, lines.append, target=PROD)
    assert any(
        "previous images are local" in line and "deploy/PRODUCTION.md" in line for line in lines
    )
    assert not any("To roll back" in line for line in lines)

    staging = local_plan(STAGING)
    printed = sr.format_plan(staging).splitlines()
    for service in STAGING.app_services:
        assert f"  {service}: unknown (not running)" in printed
    lines = []
    sr._report_recovery(sr._Progress(phase="recreating", rollback=staging.rollback), lines.append)
    assert lines == ["Rollback images:", "  none known (not running)"]


# --- a whole apply against a target whose every name differs from staging -------------------

ELSEWHERE = dataclasses.replace(
    PROD, db_service="prod-db", db_name="csa_prod", db_user="csa_prod_owner"
)


def elsewhere_runner(secret: Path) -> FakeRunner:
    state = {"migrated": False}

    def select(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        versions = ("0484", "0485", "0486") if state["migrated"] else ("0484", "0485")
        return ok("".join(f"{v}\n" for v in versions))

    def migrate(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        state["migrated"] = True
        return ok()

    def inspect_pulled(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        labels = {"org.opencontainers.image.revision": SHA}
        return ok(json.dumps([{"RepoDigests": [argv[-1]], "Config": {"Labels": labels}}]))

    return FakeRunner(
        [
            (has("rev-parse HEAD"), ok(SHA + "\n")),
            (has("status --porcelain"), ok()),
            (has("merge-base --is-ancestor"), ok()),
            (has("csa-python/versions"), ok(versions_json(DIGEST_A, tags=[SHA]))),
            (has("csa-frontend/versions"), ok(versions_json(DIGEST_B, tags=[SHA]))),
            (
                has("--profile migration-approved config --format json"),
                ok(json.dumps({"secrets": {"owner_dsn": {"file": str(secret)}}})),
            ),
            (has("config --quiet"), ok()),
            (lambda argv: argv[3:4] == ["pull"], ok()),
            (
                lambda argv: argv[3:5] == ["image", "inspect"] and "--format" not in argv,
                inspect_pulled,
            ),
            (has("inspect --format {{.Image}}"), ok(IMAGE_ONE + "\n")),
            (lambda argv: RUNNING_FORMAT in argv, ok(running_json(RUNNING_REF))),
            (lambda argv: "psql" in argv and "-c" in argv, select),
            (lambda argv: "psql" in argv and "-f" in argv, ok("0\n")),
            (has(" stop "), ok()),
            (has(" run --rm --no-deps migrate"), migrate),
            (has(" up "), ok()),
            (lambda argv: argv[3:4] == ["exec"], ok()),
            (has(" ps "), ok("gateway Up\n")),
        ]
    )


def test_apply_against_a_target_uses_only_its_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    (repo / ELSEWHERE.flags_file).write_text(FLAGS_TEXT)
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    (secret_dir / "owner_dsn").write_text("SYNTHETIC-ONLY")
    checks = tmp_path / "checks"
    checks.mkdir()
    (checks / "0486.sql").write_text("SELECT 0;\n")
    monkeypatch.setattr(
        sr,
        "discover_migrations",
        lambda: [(v, Path(f"{v}_x.sql")) for v in ("0484", "0485", "0486")],
    )
    runner = elsewhere_runner(secret_dir / "owner_dsn")
    lines: list[str] = []
    code = sr.apply(
        runner,
        repo,
        ["0486"],
        sha=SHA,
        docker_config=tmp_path / "dc",
        secret_dir=secret_dir,
        checks_dir=checks,
        sleep=lambda _: None,
        out=lines.append,
        target=ELSEWHERE,
    )
    assert code == 0, "\n".join(lines)
    assert lines[-1] == f"Rollout of {SHA[:7]} finished."
    for argv, env, _ in runner.calls:
        joined = " ".join(argv)
        assert "csa-staging" not in joined and "staging-db" not in joined, joined
        assert "csa_staging" not in joined, joined
        if env is not None:
            assert env["CSA_STAGING_PROJECT"] == "csa-prod"
            assert env["CSA_STAGING_SECRET_DIR"] == str(secret_dir)
    psql = [argv for argv, _, _ in runner.calls if "psql" in argv]
    assert psql and all(
        argv[argv.index("exec") + 2 : argv.index("exec") + 7]
        == ["prod-db", "psql", "-U", "csa_prod_owner", "-d"]
        and "csa_prod" in argv
        for argv in psql
    )
    ready = [argv for argv, _, _ in runner.calls if argv[3:4] == ["exec"]]
    assert ready and all("csa-prod-gateway-1" in argv for argv in ready)
    assert "Stopping gateway, frontend, worker, delivery…" in lines
