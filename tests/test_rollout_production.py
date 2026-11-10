"""The rollout core driven by a non-staging target: names, replicas and wording."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scripts import staging_rollout as sr
from scripts.rollout_targets import PRODUCTION, STAGING, Target
from scripts.staging_rollout import Completed, RolloutError

from tests.test_staging_rollout import (
    ALL_FLAGS,
    DIGEST_A,
    DIGEST_B,
    FLAGS_TEXT,
    FRONTEND_REF,
    PREV_SHA,
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
# A non-staging plan prints its apply command with the target, in the CLI's own order.
PROD_APPLY = ".venv/bin/python -m scripts.staging_rollout --target production apply"
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


# --- the repo switch file with an allow-listed host overlay -----------------------------------

ORIGINS = frozenset({"CSA_STAGING_CONSOLE_ORIGIN", "CSA_STAGING_HOOKS_ORIGIN"})
TUNING = frozenset({"CSA_PROD_MEM_DB", "CSA_PROD_MEM_WORKER", "CSA_PROD_WORKER_REPLICAS"})
SWITCHES = {key: value for key, value in ALL_FLAGS.items() if key not in ORIGINS}
SWITCHES_TEXT = "".join(f"{key}={value}\n" for key, value in SWITCHES.items())
HOST_REQUIRED = {
    "CSA_PROD_S3_BUCKET": "bucket-value-x",
    "CSA_PROD_S3_REGION": "region-value-x",
    "CSA_STAGING_CONSOLE_ORIGIN": "https://console.example.test",
    "CSA_STAGING_HOOKS_ORIGIN": "https://hooks.example.test",
}
HOST_TEXT = "".join(f"{key}={value}\n" for key, value in HOST_REQUIRED.items())


def hosted(host_file: Path | None, base: Target = PROD) -> Target:
    """A production-like target: origins and bucket in the host file, tuning overridable."""
    return dataclasses.replace(
        base,
        required_keys=frozenset(SWITCHES),
        optional_keys=TUNING | {"CSA_PROD_GROQ_CHAT_URL"},
        host_file=host_file,
        host_required_keys=frozenset(HOST_REQUIRED),
        host_override_keys=TUNING | {"CSA_PROD_DB_IMAGE"},
    )


def expected_digest(values: Mapping[str, str]) -> str:
    text = "".join(f"{key}={values[key]}\n" for key in sorted(values))
    return hashlib.sha256(text.encode()).hexdigest()[:12]


@pytest.mark.parametrize(
    ("content", "line", "value"),
    [
        (HOST_TEXT + "CSA_STAGING_INVITE_MAIL=true\n", 5, "true"),
        (HOST_TEXT + "CSA_PROD_GROQ_CHAT_URL=http://x\n", 5, "http://x"),
        (HOST_TEXT + "CSA_PROD_NOPE=1\n", 5, "1"),
        (HOST_TEXT + "CSA_PROD_MEM_DB=777m\nCSA_PROD_MEM_DB=777m\n", 6, "777m"),
        (HOST_TEXT + "CSA_PROD_MEM_DB\n", 5, "CSA_PROD_MEM_DB"),
        (HOST_TEXT + "not-a-name=secret-ish\n", 5, "not-a-name"),
    ],
    ids=["switch", "groq-url", "unknown", "duplicate", "malformed", "bad-name"],
)
def test_host_file_refuses_switches_groq_url_unknown_duplicate_and_malformed(
    tmp_path: Path, content: str, line: int, value: str
) -> None:
    host = tmp_path / "host.env"
    host.write_text(content)
    with pytest.raises(RolloutError) as caught:
        sr.load_host_values(hosted(host))
    assert caught.value.step == "flags"
    assert f"host.env line {line}" in caught.value.message
    assert value not in caught.value.message
    assert not any(v in caught.value.message for v in HOST_REQUIRED.values())


@pytest.mark.parametrize(
    ("line", "shown"),
    [
        ("CSA_PROD_NOPE=1", " CSA_PROD_NOPE"),
        ("CSA_STAGING_ELSE=x", " CSA_STAGING_ELSE"),
        # A secret pasted before the "=" can look like a NAME; only CSA_ keys are echoed.
        ("GHPABCDEF0123456789SECRET=1", ""),
        ("AKIAEXAMPLEKEY0000=x", ""),
        ("not-a-name=secret-ish", ""),
        ("CSA_lower=1", ""),
    ],
    ids=["csa-prod", "csa-staging", "token-like", "key-like", "not-a-name", "not-upper"],
)
def test_host_file_unknown_key_is_named_only_when_it_is_a_csa_key(
    tmp_path: Path, line: str, shown: str
) -> None:
    host = tmp_path / "host.env"
    host.write_text(HOST_TEXT + line + "\n")
    with pytest.raises(RolloutError) as caught:
        sr.load_host_values(hosted(host))
    assert (caught.value.step, caught.value.message) == (
        "flags",
        f"host.env line 5: unknown key{shown}",
    )


@pytest.mark.parametrize(
    "line",
    [
        'export CSA_STAGING_HOOKS_ORIGIN="probe-quoted-77"',
        "CSA_STAGING_HOOKS_ORIGIN='probe-quoted-77'",
        'CSA_STAGING_HOOKS_ORIGIN = "probe-quoted-77"',
    ],
    ids=["double-export", "single", "spaced"],
)
def test_host_file_refuses_quoted_values(tmp_path: Path, line: str) -> None:
    host = tmp_path / "host.env"
    text = HOST_TEXT.replace("CSA_STAGING_HOOKS_ORIGIN=https://hooks.example.test\n", "")
    host.write_text(text + line + "\n")
    with pytest.raises(RolloutError) as caught:
        sr.load_host_values(hosted(host))
    assert caught.value.step == "flags"
    assert caught.value.message == ("host.env line 4: quoted value; write KEY=value without quotes")
    assert "probe-quoted-77" not in caught.value.message


def test_host_file_missing_a_required_key_is_refused(tmp_path: Path) -> None:
    host = tmp_path / "host.env"
    host.write_text(HOST_TEXT.replace("CSA_PROD_S3_REGION=region-value-x\n", ""))
    with pytest.raises(RolloutError) as caught:
        sr.load_host_values(hosted(host))
    assert caught.value.step == "flags"
    assert caught.value.message == "host.env is missing: CSA_PROD_S3_REGION"

    host.unlink()
    with pytest.raises(RolloutError) as caught:
        sr.load_host_values(hosted(host))
    assert (caught.value.step, caught.value.message) == ("flags", "cannot read host.env")


def test_host_file_accepts_export_prefix_comments_and_tuning(tmp_path: Path) -> None:
    host = tmp_path / "host.env"
    host.write_text(
        "# tuning for this instance\n"
        "\n"
        "export CSA_PROD_S3_BUCKET=bucket-value-x\n"
        "export   CSA_PROD_S3_REGION = region-value-x\n"
        "CSA_STAGING_CONSOLE_ORIGIN=https://console.example.test\n"
        "  # indented comment\n"
        "CSA_STAGING_HOOKS_ORIGIN=https://hooks.example.test\n"
        "CSA_PROD_MEM_DB=1000m\n"
        "export CSA_PROD_DB_IMAGE=postgres:16=x\n"
    )
    assert sr.load_host_values(hosted(host)) == {
        **HOST_REQUIRED,
        "CSA_PROD_MEM_DB": "1000m",
        "CSA_PROD_DB_IMAGE": "postgres:16=x",
    }
    assert sr.load_host_values(hosted(None)) == {}
    assert sr.load_host_values(STAGING) == {}


def test_repo_file_allows_optional_tuning_keys_only_once(tmp_path: Path) -> None:
    target = hosted(None)
    path = tmp_path / "production-rollout.env"
    path.write_text(SWITCHES_TEXT)
    assert sr.load_flags(path, target) == SWITCHES

    path.write_text(SWITCHES_TEXT + "CSA_PROD_MEM_DB=640m\nCSA_PROD_GROQ_CHAT_URL=http://x\n")
    assert sr.load_flags(path, target) == {
        **SWITCHES,
        "CSA_PROD_MEM_DB": "640m",
        "CSA_PROD_GROQ_CHAT_URL": "http://x",
    }

    path.write_text(SWITCHES_TEXT + "CSA_PROD_MEM_DB=640m\nCSA_PROD_MEM_DB=700m\n")
    with pytest.raises(RolloutError) as caught:
        sr.load_flags(path, target)
    assert caught.value.message == ("production-rollout.env line 8: duplicate key CSA_PROD_MEM_DB")

    path.write_text(SWITCHES_TEXT + "CSA_PROD_S3_BUCKET=b\n")
    with pytest.raises(RolloutError) as caught:
        sr.load_flags(path, target)
    assert caught.value.message == ("production-rollout.env line 7: unknown key CSA_PROD_S3_BUCKET")

    path.write_text(SWITCHES_TEXT.replace("CSA_STAGING_INVITE_MAIL=true\n", ""))
    with pytest.raises(RolloutError) as caught:
        sr.load_flags(path, target)
    assert caught.value.message == "production-rollout.env is missing: CSA_STAGING_INVITE_MAIL"

    # Staging allows no optional key.
    path.write_text(FLAGS_TEXT + "CSA_PROD_MEM_DB=640m\n")
    with pytest.raises(RolloutError) as caught:
        sr.load_flags(path)
    assert caught.value.message == "production-rollout.env line 9: unknown key CSA_PROD_MEM_DB"


def test_host_override_wins_for_tuning_only(tmp_path: Path) -> None:
    repo_file = tmp_path / "production-rollout.env"
    repo_file.write_text(SWITCHES_TEXT + "CSA_PROD_MEM_DB=640m\nCSA_PROD_MEM_WORKER=512m\n")
    host = tmp_path / "host.env"
    host.write_text(HOST_TEXT + "CSA_PROD_MEM_DB=1000m\n")
    target = hosted(host)
    flags = sr.load_flags(repo_file, target)
    host_values = sr.load_host_values(target)
    images = {"python": PYTHON_REF, "frontend": FRONTEND_REF}
    env = sr.build_env(
        flags, images, docker_config=tmp_path / "dc", target=target, host_values=host_values
    )
    assert env["CSA_PROD_MEM_DB"] == "1000m"
    assert env["CSA_PROD_MEM_WORKER"] == "512m"
    assert env["CSA_PROD_S3_BUCKET"] == "bucket-value-x"
    assert env["CSA_STAGING_CONSOLE_ORIGIN"] == "https://console.example.test"
    assert {key: env[key] for key in SWITCHES} == SWITCHES

    # A host switch never reaches the merge.
    host.write_text(HOST_TEXT + "CSA_STAGING_INVITE_MAIL=false\n")
    with pytest.raises(RolloutError):
        sr.load_host_values(target)
    # And the merge itself refuses a host value for a repo key it may not override.
    with pytest.raises(RolloutError) as caught:
        sr.build_env(
            flags,
            images,
            docker_config=tmp_path / "dc",
            target=target,
            host_values={"CSA_STAGING_INVITE_MAIL": "false"},
        )
    assert caught.value.step == "flags"
    assert "false" not in caught.value.message


def test_plan_prints_host_digest_and_override_names(tmp_path: Path) -> None:
    target = hosted(tmp_path / "host.env")
    host_values = {**HOST_REQUIRED, "CSA_PROD_MEM_DB": "1000m", "CSA_PROD_DB_IMAGE": "pg-x"}
    images = {"python": PYTHON_REF, "frontend": FRONTEND_REF}
    steps = sr.plan_steps(["0486"], [], target=target)
    plan = sr.Plan(SHA, images, {}, ["0486"], [], [], steps, {}, host_values)
    assert "host_values" not in repr(plan) and "pg-x" not in repr(plan)
    assert sr.host_settings_digest(host_values) == expected_digest(host_values)
    digest = expected_digest(host_values)
    printed = sr.format_plan(plan, target=target)
    lines = printed.splitlines()
    assert "Host overrides: CSA_PROD_DB_IMAGE, CSA_PROD_MEM_DB" in lines
    assert f"Host settings digest: {digest}" in lines
    assert lines[-1] == f"{PROD_APPLY} --sha {SHA} --migrations 0486 --host-digest {digest}"
    assert not any(value in printed for value in ("1000m", "pg-x", *HOST_REQUIRED.values()))

    plan = sr.Plan(SHA, images, {}, [], [], [], steps, {}, dict(HOST_REQUIRED))
    lines = sr.format_plan(plan, target=target).splitlines()
    assert "Host overrides: none" in lines
    assert lines[-1] == f"{PROD_APPLY} --sha {SHA} --host-digest {expected_digest(HOST_REQUIRED)}"

    # Staging prints neither line and keeps its command.
    plan = sr.Plan(SHA, images, {}, [], [], [], steps, {})
    lines = sr.format_plan(plan).splitlines()
    assert not any(line.startswith("Host ") for line in lines)
    assert lines[-1] == f"{sr.APPLY_COMMAND} --sha {SHA}"


class HostedRig:
    """A whole apply against a hosted target whose names all differ from staging."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host_text: str) -> None:
        self.repo = tmp_path / "repo"
        (self.repo / "deploy").mkdir(parents=True)
        self.host = tmp_path / "host.env"
        self.host.write_text(host_text)
        self.target = hosted(self.host, base=ELSEWHERE)
        (self.repo / self.target.flags_file).write_text(SWITCHES_TEXT + "CSA_PROD_MEM_DB=640m\n")
        self.secret_dir = tmp_path / "secrets"
        self.secret_dir.mkdir()
        (self.secret_dir / "owner_dsn").write_text("SYNTHETIC-ONLY")
        self.checks = tmp_path / "checks"
        self.checks.mkdir()
        self.docker_config = tmp_path / "dc"
        monkeypatch.setattr(
            sr,
            "discover_migrations",
            lambda: [(v, Path(f"{v}_x.sql")) for v in ("0484", "0485", "0486")],
        )
        self.runner = elsewhere_runner(self.secret_dir / "owner_dsn")
        self.lines: list[str] = []

    def plan(self) -> sr.Plan:
        return sr.build_plan(
            self.runner,
            self.repo,
            docker_config=self.docker_config,
            secret_dir=self.secret_dir,
            checks_dir=self.checks,
            target=self.target,
        )

    def apply(self, host_digest: str | None) -> int:
        self.runner.calls.clear()
        return sr.apply(
            self.runner,
            self.repo,
            ["0486"],
            sha=SHA,
            docker_config=self.docker_config,
            secret_dir=self.secret_dir,
            checks_dir=self.checks,
            sleep=lambda _: None,
            out=self.lines.append,
            target=self.target,
            host_digest=host_digest,
        )

    def docker_calls(self) -> list[list[str]]:
        return [argv for argv in self.runner.argvs if argv[0] == "docker"]


def test_apply_refuses_when_host_digest_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = HostedRig(tmp_path, monkeypatch, HOST_TEXT + "CSA_PROD_MEM_DB=1000m\n")
    digest = sr.host_settings_digest(rig.plan().host_values)
    for wrong in ("0" * 12, None):
        rig.lines.clear()
        assert rig.apply(wrong) == 1
        assert (
            rig.lines[0] == "Stopped at confirm: host settings changed since plan; run plan again"
        )
        assert "Nothing on production was changed." in rig.lines
        assert rig.docker_calls() == []

    rig.lines.clear()
    assert rig.apply(digest) == 0, "\n".join(rig.lines)
    envs = [env for _, env, _ in rig.runner.calls if env is not None]
    assert envs and all(
        env["CSA_PROD_MEM_DB"] == "1000m" and env["CSA_PROD_S3_BUCKET"] == "bucket-value-x"
        for env in envs
    )
    assert not any(value in "\n".join(rig.lines) for value in HOST_REQUIRED.values())


def test_replica_count_change_between_plan_and_apply_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = HostedRig(tmp_path, monkeypatch, HOST_TEXT + "CSA_PROD_WORKER_REPLICAS=2\n")
    plan = rig.plan()
    assert plan.env["CSA_PROD_WORKER_REPLICAS"] == "2"
    digest = sr.host_settings_digest(plan.host_values)
    rig.host.write_text(HOST_TEXT + "CSA_PROD_WORKER_REPLICAS=1\n")
    assert rig.apply(digest) == 1
    assert rig.lines[0] == "Stopped at confirm: host settings changed since plan; run plan again"
    assert rig.docker_calls() == []


# --- the downgrade warning ---------------------------------------------------------------------

DOWNGRADE = (
    f"WARNING: {SHA[:7]} is older than the running {PREV_SHA[:7]}; this is a downgrade. "
    "A downgrade across a migration is refused."
)
CANNOT_COMPARE = f"WARNING: could not compare {SHA[:7]} with the running revision"


def warning_rig(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ancestors: Mapping[str, int],
    *,
    warn: bool = True,
    revision: str | None = PREV_SHA,
) -> HostedRig:
    """A hosted rig whose ``git merge-base --is-ancestor <sha> <R>`` answers come from ancestors.

    Only the plan-time ``HEAD origin/main`` check may run besides those; anything else fails.
    """
    rig = HostedRig(tmp_path, monkeypatch, HOST_TEXT)
    rig.target = dataclasses.replace(rig.target, downgrade_warning=warn)

    def ancestry(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        if argv[-2:] == ["HEAD", "origin/main"]:
            return ok()
        assert argv[-2] == SHA, argv
        if argv[-1] not in ancestors:
            pytest.fail(f"unexpected ancestor check: {' '.join(argv)}")
        return fail(ancestors[argv[-1]])

    rig.runner.rules[:0] = [
        (has("merge-base --is-ancestor"), ancestry),
        (lambda argv: RUNNING_FORMAT in argv, ok(running_json(RUNNING_REF, revision=revision))),
    ]
    return rig


def test_older_commit_prints_downgrade_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = warning_rig(tmp_path, monkeypatch, {PREV_SHA: 0})
    plan = rig.plan()
    assert plan.warnings == [DOWNGRADE]
    assert [argv[-3:] for argv in rig.runner.argvs if "merge-base" in argv] == [
        ["--is-ancestor", "HEAD", "origin/main"],
        ["--is-ancestor", SHA, PREV_SHA],
    ]
    lines = sr.format_plan(plan, target=rig.target).splitlines()
    start = lines.index("Images to deploy:")
    assert lines[start + 1 : start + 4] == [
        f"  python: {PYTHON_REF}",
        f"  frontend: {FRONTEND_REF}",
        DOWNGRADE,
    ]
    assert lines[start + 4] == "Rollback references (current containers):"
    assert lines.count(DOWNGRADE) == 1


def test_newer_commit_prints_no_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = warning_rig(tmp_path, monkeypatch, {PREV_SHA: 1})
    plan = rig.plan()
    assert plan.warnings == []
    assert not any(
        line.startswith("WARNING") for line in sr.format_plan(plan, target=rig.target).splitlines()
    )

    # A running image already built from the target commit needs no comparison at all.
    same = warning_rig(tmp_path / "same", monkeypatch, {}, revision=SHA)
    assert same.plan().warnings == []


@pytest.mark.parametrize(
    ("revision", "ancestors"),
    [(None, {}), (PREV_SHA, {PREV_SHA: 128})],
    ids=["no-revision-label", "git-error"],
)
def test_unknown_running_revision_warns_cannot_compare_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    revision: str | None,
    ancestors: Mapping[str, int],
) -> None:
    rig = warning_rig(tmp_path, monkeypatch, ancestors, revision=revision)
    plan = rig.plan()
    assert plan.warnings == [CANNOT_COMPARE]
    assert plan.pending == ["0486"]
    assert sr.format_plan(plan, target=rig.target).splitlines().count(CANNOT_COMPARE) == 1
    assert rig.apply(sr.host_settings_digest(plan.host_values)) == 0, "\n".join(rig.lines)


def test_staging_never_runs_the_ancestor_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = warning_rig(tmp_path, monkeypatch, {}, warn=False)
    plan = rig.plan()
    assert plan.warnings == []
    assert [argv for argv in rig.runner.argvs if "merge-base" in argv] == [
        ["git", "-C", str(rig.repo), "merge-base", "--is-ancestor", "HEAD", "origin/main"]
    ]
    assert "WARNING" not in sr.format_plan(plan, target=rig.target)


def test_downgrade_across_a_migration_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = warning_rig(tmp_path, monkeypatch, {PREV_SHA: 0})
    applied = ok("0484\n0485\n0486\n0487\n")
    rig.runner.rules.insert(0, (lambda argv: "psql" in argv and "-c" in argv, applied))
    with pytest.raises(RolloutError) as caught:
        rig.plan()
    assert caught.value.step == "plan"
    assert caught.value.message == (
        "production has migrations this checkout lacks: 0487; "
        "update the checkout before rolling out"
    )


# --- the backup before anything changes --------------------------------------------------------

BACKUP_ARGV = ["/srv/csa-prod/backup/backup.sh"]
BACKUP_TEXT = "#!/bin/bash\nset -euo pipefail\n# repository copy\n"
# The script stamps its log line when it starts, at whole seconds; apply starts mid-second.
STARTED = datetime(2026, 11, 10, 6, 0, 1, 500000, tzinfo=UTC)
TS_AT_START = "20261110T060001Z"
TS_BEFORE_START = "20261110T060000Z"
OBJECT_AT_START = f"backups/csa_staging-{TS_AT_START}.dump"
BACKUP_FAILED = (
    "Nothing on production was changed. The backup step failed; "
    "the deploy stopped before any change."
)
REINSTALL = "install -m 0755 deploy/production/backup.sh /srv/csa-prod/backup/backup.sh"


class BackupBox:
    """The repository copy, the installed copy and the log, with a backup that appends ``line``."""

    def __init__(self, tmp_path: Path, repo: Path, line: str | None, code: int = 0) -> None:
        self.repo = repo
        tmp_path.mkdir(parents=True, exist_ok=True)
        (repo / "deploy" / "production").mkdir(parents=True, exist_ok=True)
        (repo / "deploy" / "production" / "backup.sh").write_text(BACKUP_TEXT)
        self.installed = tmp_path / "installed-backup.sh"
        self.installed.write_text(BACKUP_TEXT)
        self.log = tmp_path / "backup.log"
        self.log.write_text("20261110T000001Z ok 2923724 bytes\n")
        self.line = line
        self.code = code

    def answer(self, argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        if self.line is not None:
            with self.log.open("a") as log:
                log.write(self.line + "\n")
        return Completed(self.code, "", "")

    def run(self, runner: FakeRunner, target: Target | None = None) -> sr.BackupResult | None:
        return sr.run_pre_change(
            runner,
            target or BACKED_UP,
            self.repo,
            now=lambda: STARTED,
            log_path=self.log,
            installed=self.installed,
        )


BACKED_UP = dataclasses.replace(PROD, pre_change=tuple(BACKUP_ARGV))


def backup_runner(box: BackupBox) -> FakeRunner:
    return FakeRunner([(lambda argv: list(argv) == BACKUP_ARGV, box.answer)])


def test_backup_ok_at_the_start_second_passes(tmp_path: Path) -> None:
    box = BackupBox(tmp_path, tmp_path / "repo", f"{TS_AT_START} ok 3097983 bytes")
    runner = backup_runner(box)
    assert box.run(runner) == sr.BackupResult(TS_AT_START, OBJECT_AT_START)
    assert runner.argvs == [BACKUP_ARGV]
    # Its environment is the rollout's own: nothing added, nothing from the host file.
    assert runner.calls[0][1] is None


def test_backup_skipped_line_refuses(tmp_path: Path) -> None:
    line = f"{TS_AT_START} skipped: previous backup still running"
    box = BackupBox(tmp_path, tmp_path / "repo", line)
    with pytest.raises(RolloutError) as caught:
        box.run(backup_runner(box))
    assert caught.value.step == "backup"
    assert line in caught.value.message


def test_backup_stale_ok_line_refuses(tmp_path: Path) -> None:
    line = f"{TS_BEFORE_START} ok 3097983 bytes"
    box = BackupBox(tmp_path, tmp_path / "repo", line)
    with pytest.raises(RolloutError) as caught:
        box.run(backup_runner(box))
    assert caught.value.step == "backup"
    assert line in caught.value.message

    # The script appended nothing: the last line is an older run's.
    box = BackupBox(tmp_path / "quiet", tmp_path / "quiet-repo", None)
    with pytest.raises(RolloutError) as caught:
        box.run(backup_runner(box))
    assert "20261110T000001Z ok 2923724 bytes" in caught.value.message


@pytest.mark.parametrize(
    "line",
    [
        "2026-11-10T06:00:01+00:00 ok 3097983 bytes",
        "20261110T060001 ok 3097983 bytes",
        "20261399T060001Z ok 3097983 bytes",
        "ok",
        "less than 2 GB free in /var/tmp",
    ],
    ids=["iso-extended", "no-zone", "bad-date", "no-timestamp", "an-error"],
)
def test_backup_unparseable_timestamp_refuses(tmp_path: Path, line: str) -> None:
    box = BackupBox(tmp_path, tmp_path / "repo", line)
    with pytest.raises(RolloutError) as caught:
        box.run(backup_runner(box))
    assert caught.value.step == "backup"
    assert line in caught.value.message


def test_backup_missing_or_empty_log_refuses(tmp_path: Path) -> None:
    box = BackupBox(tmp_path, tmp_path / "repo", None)
    box.log.unlink()
    with pytest.raises(RolloutError) as caught:
        box.run(backup_runner(box))
    assert caught.value.step == "backup"
    assert "backup.log" in caught.value.message

    for empty in ("", "\n\n  \n"):
        box.log.write_text(empty)
        with pytest.raises(RolloutError) as caught:
            box.run(backup_runner(box))
        assert caught.value.step == "backup"
        assert "backup.log" in caught.value.message

    box.log.unlink()
    box.log.mkdir()
    with pytest.raises(RolloutError) as caught:
        box.run(backup_runner(box))
    assert caught.value.step == "backup"


def test_backup_reads_only_the_tail_of_a_long_log(tmp_path: Path) -> None:
    box = BackupBox(tmp_path, tmp_path / "repo", f"{TS_AT_START} ok 3097983 bytes\n\n")
    box.log.write_text("20261101T000001Z ok 1 bytes\n" * 50_000)
    assert box.run(backup_runner(box)) == sr.BackupResult(TS_AT_START, OBJECT_AT_START)


def test_backup_nonzero_exit_refuses(tmp_path: Path) -> None:
    box = BackupBox(tmp_path, tmp_path / "repo", f"{TS_AT_START} ok 3097983 bytes", code=1)
    with pytest.raises(RolloutError) as caught:
        box.run(backup_runner(box))
    assert caught.value.step == "backup"
    assert "exit 1" in caught.value.message


def test_backup_installed_copy_must_match_repo(tmp_path: Path) -> None:
    box = BackupBox(tmp_path, tmp_path / "repo", f"{TS_AT_START} ok 3097983 bytes")
    box.installed.write_text(BACKUP_TEXT + "# edited on the instance\n")
    runner = backup_runner(box)
    with pytest.raises(RolloutError) as caught:
        box.run(runner)
    assert caught.value.step == "backup"
    assert "reinstall" in caught.value.message
    assert REINSTALL in caught.value.message
    assert runner.argvs == []

    box.installed.unlink()
    with pytest.raises(RolloutError) as caught:
        box.run(runner)
    assert "reinstall" in caught.value.message
    assert REINSTALL in caught.value.message
    assert runner.argvs == []


@pytest.mark.parametrize(
    "argv",
    [
        ("sudo", "/srv/csa-prod/backup/backup.sh"),
        ("/usr/bin/sudo", "-n", "/srv/csa-prod/backup/backup.sh"),
        ("/srv/csa-prod/backup/backup.sh", "sudo"),
    ],
    ids=["sudo", "sudo-path", "sudo-argument"],
)
def test_pre_change_argv_never_contains_sudo(tmp_path: Path, argv: tuple[str, ...]) -> None:
    assert PRODUCTION.pre_change is not None
    assert not any(Path(part).name == "sudo" for part in PRODUCTION.pre_change)
    assert sr.BACKUP_INSTALLED == Path(PRODUCTION.pre_change[0])

    box = BackupBox(tmp_path, tmp_path / "repo", f"{TS_AT_START} ok 3097983 bytes")
    runner = FakeRunner([(lambda _: True, ok())])
    with pytest.raises(RolloutError) as caught:
        box.run(runner, dataclasses.replace(PROD, pre_change=argv))
    assert caught.value.step == "backup"
    assert "sudo" in caught.value.message
    assert runner.argvs == []


def test_no_pre_change_does_nothing(tmp_path: Path) -> None:
    runner = FakeRunner([])
    for target in (STAGING, PROD):
        assert (
            sr.run_pre_change(
                runner,
                target,
                tmp_path / "no-repo",
                now=lambda: pytest.fail("no clock without a backup"),
                log_path=tmp_path / "no-log",
                installed=tmp_path / "no-installed",
            )
            is None
        )
    assert runner.calls == []


class BackupRig(HostedRig):
    """A hosted apply whose target takes a backup first."""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        line: str | None,
        code: int = 0,
    ) -> None:
        super().__init__(tmp_path, monkeypatch, HOST_TEXT)
        self.target = dataclasses.replace(self.target, pre_change=tuple(BACKUP_ARGV))
        self.box = BackupBox(tmp_path, self.repo, line, code)
        self.runner.rules.insert(0, (lambda argv: list(argv) == BACKUP_ARGV, self.box.answer))

    def apply(self, host_digest: str | None) -> int:
        self.runner.calls.clear()
        return sr.apply(
            self.runner,
            self.repo,
            ["0486"],
            sha=SHA,
            docker_config=self.docker_config,
            secret_dir=self.secret_dir,
            checks_dir=self.checks,
            sleep=lambda _: None,
            out=self.lines.append,
            target=self.target,
            host_digest=host_digest,
            now=lambda: STARTED,
            backup_log=self.box.log,
            backup_installed=self.box.installed,
        )

    def digest(self) -> str:
        return sr.host_settings_digest(self.plan().host_values)


def test_backup_ok_after_start_passes_and_names_the_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = BackupRig(tmp_path, monkeypatch, f"{TS_AT_START} ok 3097983 bytes")
    assert rig.apply(rig.digest()) == 0, "\n".join(rig.lines)
    assert f"Backup taken: {OBJECT_AT_START}" in rig.lines
    order = [line for line in rig.lines if line.endswith("…") or line.startswith("Backup taken")]
    assert order[:4] == [
        "Checking compose config…",
        "Taking a backup…",
        f"Backup taken: {OBJECT_AT_START}",
        "Pulling and verifying images…",
    ]
    argvs = rig.runner.argvs
    backup = argvs.index(BACKUP_ARGV)
    assert any("config --quiet" in " ".join(argv) for argv in argvs[:backup])
    assert argvs[backup + 1][3:4] == ["pull"]
    assert argvs.count(BACKUP_ARGV) == 1


@pytest.mark.parametrize(
    ("line", "code"),
    [
        (f"{TS_AT_START} ok 3097983 bytes", 1),
        (f"{TS_AT_START} skipped: previous backup still running", 0),
    ],
    ids=["nonzero-exit", "skipped"],
)
def test_backup_nonzero_exit_refuses_before_any_docker_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str, code: int
) -> None:
    rig = BackupRig(tmp_path, monkeypatch, line, code)
    assert rig.apply(rig.digest()) == 1
    # Plan's reads and the compose config check come first; nothing runs after the backup.
    assert rig.runner.argvs[-1] == BACKUP_ARGV
    assert not any(
        argv[3:4] in (["pull"], ["exec"]) or {"stop", "up", "run"} & set(argv)
        for argv in rig.docker_calls()
    )
    assert rig.lines[rig.lines.index("Taking a backup…") + 1].startswith("Stopped at backup: ")
    assert BACKUP_FAILED in rig.lines
    assert "Nothing on production was changed." not in rig.lines
    assert not any(line.startswith("Backup taken") for line in rig.lines)


def test_installed_copy_mismatch_stops_apply_before_the_backup_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = BackupRig(tmp_path, monkeypatch, f"{TS_AT_START} ok 3097983 bytes")
    rig.box.installed.write_text("#!/bin/bash\n# an older copy\n")
    assert rig.apply(rig.digest()) == 1
    assert BACKUP_ARGV not in rig.runner.argvs
    assert any(REINSTALL in line for line in rig.lines)
    assert BACKUP_FAILED in rig.lines


def test_recovery_after_migration_names_the_backup_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = BackupRig(tmp_path, monkeypatch, f"{TS_AT_START} ok 3097983 bytes")
    rig.runner.rules.insert(0, (lambda argv: argv[3:4] == ["exec"], fail(1)))
    assert rig.apply(rig.digest()) == 1
    named = (
        f"The backup taken at the start of this deploy is {OBJECT_AT_START} "
        "(restore: deploy/PRODUCTION.md)"
    )
    assert rig.lines[-3:] == [
        "Migrations were applied: 0486. The previous images fail /readyz against this schema, "
        "so do not roll back; fix forward, then re-run plan and apply.",
        named,
        sr.RERUN_HINT,
    ]

    # Without a migration there is nothing to restore from: the rollback lines stand alone.
    lines: list[str] = []
    progress = sr._Progress(phase="up", recorded=[], backup=OBJECT_AT_START)
    sr._report_recovery(progress, lines.append, target=PROD)
    assert not any("backup" in line for line in lines)

    # When the migration state is unknown the backup is named too.
    lines.clear()
    progress = sr._Progress(phase="stopped", recorded=None, pending=["0486"])
    progress.backup = OBJECT_AT_START
    sr._report_recovery(progress, lines.append, target=PROD)
    assert lines[-1] == named


def test_production_plan_never_runs_a_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = BackupRig(tmp_path, monkeypatch, f"{TS_AT_START} ok 3097983 bytes")
    log_before = rig.box.log.read_text()
    rig.plan()
    assert BACKUP_ARGV not in rig.runner.argvs
    assert rig.box.log.read_text() == log_before


def _relative(rig: HostedRig, root: Path) -> tuple[list[str], list[list[str]]]:
    """The rig's output lines and argvs with its temporary directory written as ``<tmp>``."""
    lines = [line.replace(str(root), "<tmp>") for line in rig.lines]
    argvs = [[part.replace(str(root), "<tmp>") for part in argv] for argv in rig.runner.argvs]
    return lines, argvs


def test_target_without_pre_change_applies_exactly_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backed = BackupRig(tmp_path / "backed", monkeypatch, f"{TS_AT_START} ok 3097983 bytes")
    assert backed.apply(backed.digest()) == 0, "\n".join(backed.lines)
    plain = BackupRig(tmp_path / "plain", monkeypatch, f"{TS_AT_START} ok 3097983 bytes")
    plain.target = dataclasses.replace(plain.target, pre_change=None)
    plain.box.installed.unlink()  # never read without a backup
    assert plain.apply(plain.digest()) == 0, "\n".join(plain.lines)
    backed_lines, backed_argvs = _relative(backed, tmp_path / "backed")
    plain_lines, plain_argvs = _relative(plain, tmp_path / "plain")
    added = ["Taking a backup…", f"Backup taken: {OBJECT_AT_START}"]
    assert [line for line in backed_lines if line not in added] == plain_lines
    assert [argv for argv in backed_argvs if argv != BACKUP_ARGV] == plain_argvs
    assert BACKUP_ARGV not in plain_argvs
    assert not any("ackup" in line for line in plain_lines)

    # A failure before any change still prints the plain recovery line.
    lines: list[str] = []
    sr._report_recovery(sr._Progress(), lines.append, target=PROD)
    assert lines == ["Nothing on production was changed."]
