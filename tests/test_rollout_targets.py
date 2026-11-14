from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest
import scripts.staging_rollout as sr
from scripts.rollout_targets import PROD_TUNING_KEYS, PRODUCTION, STAGING, get_target

from tests.test_rollout_gate import FakeDeployments, deployment
from tests.test_rollout_production import HOST_TEXT
from tests.test_staging_rollout import (
    DIGEST_A,
    DIGEST_B,
    SHA,
    FakeRunner,
    fail,
    has,
    make_secret_files,
    ok,
    staging_runner,
    versions_json,
)


def test_staging_target_matches_current_module_constants() -> None:
    assert STAGING.name == "staging"
    assert STAGING.project == sr.PROJECT
    assert STAGING.compose_files == (
        "deploy/compose.staging-local-proposal.yml",
        "deploy/compose.staging-shared-caddy.yml",
        "deploy/compose.staging-pipeline.yml",
    )
    assert STAGING.profiles == ("staging-approved", "pipeline-approved")
    assert STAGING.app_services == sr.APP_SERVICES
    assert STAGING.required_keys == sr.FLAG_KEYS
    assert STAGING.migration_approval == sr.MIGRATION_APPROVAL
    assert STAGING.db_service == "staging-db"
    assert STAGING.db_name == "csa_staging"
    assert STAGING.staged_gate is False
    assert STAGING.pre_change is None


def test_get_target_returns_staging() -> None:
    assert get_target("staging") is STAGING


def test_get_target_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="prod"):
        get_target("prod")


def test_staging_compose_argv_and_env_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert sr.compose_base() == [
        "docker",
        "--host",
        "unix:///var/run/docker.sock",
        "compose",
        "--env-file",
        "/dev/null",
        "--project-name",
        "csa-staging",
        "--file",
        "deploy/compose.staging-local-proposal.yml",
        "--file",
        "deploy/compose.staging-shared-caddy.yml",
        "--file",
        "deploy/compose.staging-pipeline.yml",
        "--profile",
        "staging-approved",
        "--profile",
        "pipeline-approved",
    ]
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/someone")
    flags = {"CSA_STAGING_INVITE_MAIL": "true"}
    images = {
        "python": "ghcr.io/o/csa-python@sha256:aa",
        "frontend": "ghcr.io/o/csa-frontend@sha256:bb",
    }
    env = sr.build_env(
        flags, images, docker_config=tmp_path / "dc", secret_dir=tmp_path / "secrets"
    )
    assert env == {
        "PATH": "/usr/bin",
        "HOME": "/home/someone",
        "NO_LOCAL_ENV": "1",
        "ENV": "ci",
        "COMPOSE_PROFILES": "",
        "CSA_STAGING_PROJECT": "csa-staging",
        "CSA_STAGING_SECRET_DIR": str(tmp_path / "secrets"),
        "DOCKER_CONFIG": str(tmp_path / "dc"),
        "CSA_STAGING_PYTHON_IMAGE": "ghcr.io/o/csa-python@sha256:aa",
        "CSA_STAGING_FRONTEND_IMAGE": "ghcr.io/o/csa-frontend@sha256:bb",
        "CSA_STAGING_INVITE_MAIL": "true",
    }


# --- the production target ---------------------------------------------------------------------

ORIGINS = frozenset({"CSA_STAGING_CONSOLE_ORIGIN", "CSA_STAGING_HOOKS_ORIGIN"})
SIX_SWITCHES = STAGING.required_keys - ORIGINS


def test_production_target_values() -> None:
    assert get_target("production") is PRODUCTION
    assert PRODUCTION.name == "production"
    assert PRODUCTION.project == "csa-prod"
    assert PRODUCTION.secret_dir == Path("/srv/csa-prod/secrets")
    assert PRODUCTION.compose_files == (*STAGING.compose_files, "deploy/compose.production.yml")
    assert PRODUCTION.profiles == ("staging-approved", "pipeline-approved")
    assert PRODUCTION.replicas == {"worker": ("CSA_PROD_WORKER_REPLICAS", 2)}
    assert PRODUCTION.flags_file == "deploy/production-rollout.env"
    assert PRODUCTION.required_keys == SIX_SWITCHES
    assert len(PRODUCTION.required_keys) == 6
    assert PRODUCTION.optional_keys == PROD_TUNING_KEYS | {
        "CSA_STAGING_TRAINING_VIDEO",
        "CSA_PROD_S3_TRAINING_KEY",
    }
    assert PRODUCTION.host_file == Path("/srv/csa-prod/host.env")
    assert PRODUCTION.host_required_keys == {
        "CSA_PROD_S3_BUCKET",
        "CSA_PROD_S3_REGION",
        *ORIGINS,
    }
    assert PRODUCTION.host_override_keys == PROD_TUNING_KEYS | {"CSA_PROD_DB_IMAGE"}
    assert PRODUCTION.staged_gate is True
    assert PRODUCTION.downgrade_warning is True
    assert PRODUCTION.live_checkout == "/home/ubuntu/CSAV2"
    assert PRODUCTION.pre_change == ("/srv/csa-prod/backup/backup.sh",)


def test_staging_still_requires_its_eight_keys() -> None:
    assert STAGING.required_keys == SIX_SWITCHES | ORIGINS
    assert len(STAGING.required_keys) == 8
    assert STAGING.host_file is None
    assert sr.load_flags(sr.REPO_ROOT / STAGING.flags_file).keys() == STAGING.required_keys


def test_production_database_names_match_the_running_instance() -> None:
    assert (PRODUCTION.db_service, PRODUCTION.db_name, PRODUCTION.db_user) == (
        "staging-db",
        "csa_staging",
        "csa",
    )
    assert sr.container_names(PRODUCTION, {}, "staging-db") == ["csa-prod-staging-db-1"]


def test_every_compose_prod_variable_is_accounted_for() -> None:
    text = (sr.REPO_ROOT / "deploy/compose.production.yml").read_text(encoding="utf-8")
    # A name ending in "_" is a wildcard in a comment (CSA_PROD_MEM_*), not a variable.
    names = {name for name in re.findall(r"CSA_PROD_[A-Z0-9_]+", text) if not name.endswith("_")}
    assert names, "no CSA_PROD_ variable found"
    accounted = (
        PROD_TUNING_KEYS
        | PRODUCTION.optional_keys
        | PRODUCTION.host_required_keys
        | {"CSA_PROD_DB_IMAGE"}
        | {"CSA_PROD_GROQ_CHAT_URL"}  # excluded on purpose: fixed in the compose file
    )
    assert sorted(names - accounted) == []


def test_groq_url_is_in_no_allow_list() -> None:
    for keys in (
        PRODUCTION.required_keys,
        PRODUCTION.optional_keys,
        PRODUCTION.host_override_keys,
        PRODUCTION.host_required_keys,
    ):
        assert "CSA_PROD_GROQ_CHAT_URL" not in keys


def test_production_env_file_loads() -> None:
    path = sr.REPO_ROOT / PRODUCTION.flags_file
    flags = sr.load_flags(path, PRODUCTION)
    assert flags.keys() == SIX_SWITCHES | {"CSA_STAGING_TRAINING_VIDEO", "CSA_PROD_S3_TRAINING_KEY"}
    assert flags == {
        "CSA_STAGING_WORKSPACE_ASSISTANT": "true",
        "CSA_STAGING_GATEWAY_MODEL_SECRET": "staging_model_key",
        "CSA_STAGING_INVITE_MAIL": "true",
        "CSA_STAGING_EMAIL_ASSISTANT": "true",
        "CSA_STAGING_BOOKINGS_GOOGLE_CALENDAR": "true",
        "CSA_STAGING_BOOKINGS_GOOGLE_MEET": "true",
        "CSA_STAGING_TRAINING_VIDEO": "true",
        "CSA_PROD_S3_TRAINING_KEY": "prod_s3_training",
    }
    text = path.read_text(encoding="utf-8")
    assert not any(marker in text for marker in ("http", "://", ".fourstacks"))


# --- the optional Jev switches, for every target -----------------------------------------------


VIDEO_KEYS = ("CSA_STAGING_TRAINING_VIDEO=", "CSA_PROD_S3_TRAINING_KEY=")


def without_video_lines(text: str) -> str:
    """The switch file minus its training-video lines, so a test can add its own."""
    return "".join(
        line for line in text.splitlines(keepends=True) if not line.startswith(VIDEO_KEYS)
    )


def production_flags_with(tmp_path: Path, *lines: str) -> Path:
    """The real production switch file, without its video lines, plus ``lines``."""
    text = without_video_lines((sr.REPO_ROOT / PRODUCTION.flags_file).read_text(encoding="utf-8"))
    path = tmp_path / "production-rollout.env"
    path.write_text(text + "".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def test_jev_switches_are_in_no_target_key_set() -> None:
    for target in (STAGING, PRODUCTION):
        for keys in (target.required_keys, target.host_required_keys, target.host_override_keys):
            assert not keys & sr.OPTIONAL_FLAG_KEYS


def test_production_flags_accept_a_valid_jev_switch(tmp_path: Path) -> None:
    path = production_flags_with(tmp_path, "CSA_STAGING_ASSISTANT_DECIDER_EMAIL=llm")
    flags = sr.load_flags(path, PRODUCTION)
    assert flags["CSA_STAGING_ASSISTANT_DECIDER_EMAIL"] == "llm"
    assert flags.keys() == SIX_SWITCHES | {"CSA_STAGING_ASSISTANT_DECIDER_EMAIL"}


def test_production_flags_refuse_an_invalid_jev_value(tmp_path: Path) -> None:
    path = production_flags_with(tmp_path, "CSA_STAGING_ASSISTANT_DECIDER_EMAIL=always")
    with pytest.raises(sr.RolloutError) as caught:
        sr.load_flags(path, PRODUCTION)
    assert caught.value.message.endswith(
        "CSA_STAGING_ASSISTANT_DECIDER_EMAIL must be one of auto, llm, shadow, cascade"
    )
    assert "always" not in caught.value.message


def test_production_flags_refuse_shadow_without_the_jev_key(tmp_path: Path) -> None:
    path = production_flags_with(tmp_path, "CSA_STAGING_ASSISTANT_DECIDER_EMAIL=shadow")
    with pytest.raises(sr.RolloutError) as caught:
        sr.load_flags(path, PRODUCTION)
    assert caught.value.message == (
        "production-rollout.env: CSA_STAGING_ASSISTANT_DECIDER_EMAIL=shadow needs "
        "CSA_STAGING_JEV_SECRET=staging_jev_key"
    )


def test_staging_still_requires_its_eight_with_a_jev_switch_set(tmp_path: Path) -> None:
    path = production_flags_with(tmp_path, "CSA_STAGING_JEV_FLOOR_NOUL=0.8")
    with pytest.raises(sr.RolloutError, match="is missing: CSA_STAGING_CONSOLE_ORIGIN, "):
        sr.load_flags(path, STAGING)


def test_host_file_refuses_a_jev_switch(tmp_path: Path) -> None:
    host = tmp_path / "host.env"
    host.write_text(HOST_TEXT + "CSA_STAGING_ASSISTANT_DECIDER_EMAIL=llm\n", encoding="utf-8")
    with pytest.raises(sr.RolloutError, match="unknown key CSA_STAGING_ASSISTANT_DECIDER_EMAIL"):
        sr.load_host_values(dataclasses.replace(PRODUCTION, host_file=host))


def test_production_jev_switch_reaches_compose(tmp_path: Path) -> None:
    path = production_flags_with(
        tmp_path,
        "CSA_STAGING_JEV_SECRET=staging_jev_key",
        "CSA_STAGING_ASSISTANT_DECIDER_ROUTING=cascade",
    )
    flags = sr.load_flags(path, PRODUCTION)
    images = {"python": "p@sha256:aa", "frontend": "f@sha256:bb"}
    env = sr.build_env(flags, images, docker_config=tmp_path / "dc", target=PRODUCTION)
    assert env["CSA_STAGING_JEV_SECRET"] == "staging_jev_key"
    assert env["CSA_STAGING_ASSISTANT_DECIDER_ROUTING"] == "cascade"


# --- --target on the command line ---------------------------------------------------------------


def first_docker(runner: FakeRunner) -> list[str]:
    return next(argv for argv in runner.argvs if argv[0] == "docker")


def test_main_target_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Production: the gate passes, then the first docker command fails so plan stops there.
    host = tmp_path / "host.env"
    host.write_text(HOST_TEXT)
    hosted = dataclasses.replace(PRODUCTION, host_file=host)
    monkeypatch.setattr(sr, "get_target", lambda name: {"production": hosted}[name])
    runner = FakeRunner(
        [
            *FakeDeployments(deployment(11, SHA, "success")).rules(),
            (has("rev-parse HEAD"), ok(SHA + "\n")),
            (has("status --porcelain"), ok()),
            (has("merge-base --is-ancestor"), ok()),
            (has("csa-python/versions"), ok(versions_json(DIGEST_A, tags=[SHA]))),
            (has("csa-frontend/versions"), ok(versions_json(DIGEST_B, tags=[SHA]))),
            (lambda argv: argv[0] == "docker", fail(1)),
        ]
    )
    assert sr.main(["--target", "production", "plan"], runner=runner) == 1
    docker = first_docker(runner)
    assert docker[docker.index("--project-name") + 1] == "csa-prod"
    assert "deploy/compose.production.yml" in docker


def test_main_defaults_to_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_dir = tmp_path / "secrets"
    checks_dir = tmp_path / "checks"
    checks_dir.mkdir()
    monkeypatch.setattr(sr, "SECRET_DIR", secret_dir)
    monkeypatch.setattr(sr, "CHECKS_DIR", checks_dir)
    monkeypatch.setattr(
        sr, "discover_migrations", lambda: [(v, Path(f"{v}_x.sql")) for v in ("0484", "0485")]
    )
    runner = staging_runner(secret_files=make_secret_files(secret_dir, "staging_owner_dsn"))
    assert sr.main(["plan"], runner=runner) == 0
    docker = first_docker(runner)
    assert docker[docker.index("--project-name") + 1] == "csa-staging"
    assert capsys.readouterr().out.splitlines()[-1] == f"{sr.APPLY_COMMAND} --sha {SHA}"


def test_main_rejects_an_unknown_target(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        sr.main(["--target", "prod", "plan"], runner=FakeRunner([]))
    assert caught.value.code == 2


def test_hosted_apply_without_host_digest_is_an_argparse_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = FakeRunner([])
    with pytest.raises(SystemExit) as caught:
        sr.main(["--target", "production", "apply", "--sha", SHA], runner=runner)
    assert caught.value.code == 2
    assert "--host-digest" in capsys.readouterr().err
    assert runner.calls == []


@pytest.mark.parametrize("digest", ["0" * 11, "0" * 13, "A" * 12, "g" * 12])
def test_host_digest_must_be_twelve_lowercase_hex(digest: str) -> None:
    runner = FakeRunner([])
    argv = ["--target", "production", "apply", "--sha", SHA, "--host-digest", digest]
    with pytest.raises(SystemExit) as caught:
        sr.main(argv, runner=runner)
    assert caught.value.code == 2
    assert runner.calls == []


def test_staging_apply_takes_no_host_digest(capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner([])
    with pytest.raises(SystemExit) as caught:
        sr.main(["apply", "--sha", SHA, "--host-digest", "0" * 12], runner=runner)
    assert caught.value.code == 2
    assert runner.calls == []


# --- the training worker -----------------------------------------------------------------------


def test_both_targets_recreate_and_stop_the_training_worker() -> None:
    for target in (STAGING, PRODUCTION):
        assert "training-worker" in target.app_services, target.name
        assert "training-worker" in target.stop_services, target.name
        assert set(target.app_services) == set(target.stop_services), target.name
        # One process: never scaled by a replica variable.
        assert "training-worker" not in target.replicas, target.name
    assert sr.SERVICE_APP["training-worker"] == "python"
    assert set(sr.SERVICE_APP) == set(STAGING.app_services)
    assert "training-worker" in sr.STOPPED_NAMES


def test_the_training_switches_are_optional_and_their_values_are_checked(tmp_path: Path) -> None:
    assert "CSA_PROD_MEM_TRAINING_WORKER" in PROD_TUNING_KEYS
    assert STAGING.optional_keys == {"CSA_STAGING_TRAINING_VIDEO"}
    # The key switch is production's alone: staging has no S3.
    assert "CSA_PROD_S3_TRAINING_KEY" not in STAGING.optional_keys
    assert "CSA_PROD_S3_TRAINING_KEY" not in PRODUCTION.host_override_keys
    path = production_flags_with(
        tmp_path, "CSA_STAGING_TRAINING_VIDEO=true", "CSA_PROD_S3_TRAINING_KEY=prod_s3_training"
    )
    flags = sr.load_flags(path, PRODUCTION)
    assert flags["CSA_STAGING_TRAINING_VIDEO"] == "true"
    env = sr.build_env(
        flags,
        {"python": "p@sha256:aa", "frontend": "f@sha256:bb"},
        docker_config=tmp_path / "dc",
        target=PRODUCTION,
    )
    assert env["CSA_STAGING_TRAINING_VIDEO"] == "true"
    assert env["CSA_PROD_S3_TRAINING_KEY"] == "prod_s3_training"


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (
            "CSA_STAGING_TRAINING_VIDEO=yes",
            "CSA_STAGING_TRAINING_VIDEO must be one of false, true",
        ),
        # Any other declared secret would hand the training worker another key.
        (
            "CSA_PROD_S3_TRAINING_KEY=prod_s3_app",
            "CSA_PROD_S3_TRAINING_KEY must be one of prod_s3_training_disabled, prod_s3_training",
        ),
    ],
)
def test_production_flags_refuse_a_bad_training_value(
    tmp_path: Path, line: str, message: str
) -> None:
    path = production_flags_with(tmp_path, line)
    with pytest.raises(sr.RolloutError) as caught:
        sr.load_flags(path, PRODUCTION)
    assert caught.value.message.endswith(message)
    assert line.split("=", 1)[1] not in caught.value.message


def test_staging_flags_refuse_the_production_key_switch(tmp_path: Path) -> None:
    text = (sr.REPO_ROOT / STAGING.flags_file).read_text(encoding="utf-8")
    path = tmp_path / "staging-rollout.env"
    path.write_text(text + "CSA_STAGING_TRAINING_VIDEO=false\n", encoding="utf-8")
    assert sr.load_flags(path, STAGING)["CSA_STAGING_TRAINING_VIDEO"] == "false"
    path.write_text(text + "CSA_PROD_S3_TRAINING_KEY=prod_s3_training\n", encoding="utf-8")
    with pytest.raises(sr.RolloutError, match="unknown key CSA_PROD_S3_TRAINING_KEY"):
        sr.load_flags(path, STAGING)
