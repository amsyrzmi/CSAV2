from __future__ import annotations

import dataclasses
import re
import shutil
from pathlib import Path

import pytest
import scripts.staging_rollout as sr
from scripts.rollout_targets import PROD_TUNING_KEYS, PRODUCTION, STAGING, get_target

from tests.test_production_overlay import _secret_dir
from tests.test_rollout_gate import FakeDeployments, deployment
from tests.test_rollout_production import HOST_REQUIRED, HOST_TEXT
from tests.test_staging_rollout import (
    DIGEST_A,
    DIGEST_B,
    PREV_SHA,
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


def test_staging_targets_downgrade_warning_is_on() -> None:
    assert STAGING.downgrade_warning is True
    assert PRODUCTION.downgrade_warning is True


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


def test_package_scope_and_registry_login_paths() -> None:
    assert STAGING.package_scope == "users/amsyrzmi"
    assert PRODUCTION.package_scope == STAGING.package_scope
    assert STAGING.registry_auth == Path("/srv/csa-staging/registry/config.json")
    assert PRODUCTION.registry_auth == Path("/srv/csa-prod/registry/config.json")
    assert PRODUCTION.registry_auth != STAGING.registry_auth


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
        "CSA_STAGING_HR_DEFAULT_TEMPLATES",
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
    flags = sr.load_flags(sr.REPO_ROOT / STAGING.flags_file)
    # The optional switches (training video, the Jev key) may or may not be in the checked-in file.
    optional = STAGING.optional_keys | {"CSA_STAGING_JEV_SECRET"}
    assert flags.keys() <= STAGING.required_keys | optional
    assert flags.keys() - optional == STAGING.required_keys


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
    # Jev runs in shadow on production: Groq decides, Jev's answers are only logged. The customer
    # classifier default is not set, so it stays on the LLM.
    jev = {
        "CSA_STAGING_JEV_SECRET": "staging_jev_key",
        "CSA_STAGING_ASSISTANT_DECIDER_EMAIL": "shadow",
        "CSA_STAGING_ASSISTANT_DECIDER_ROUTING": "shadow",
        "CSA_STAGING_ASSISTANT_DECIDER_INBOUND_INJECTION": "shadow",
    }
    assert flags.keys() == (
        SIX_SWITCHES | {"CSA_STAGING_TRAINING_VIDEO", "CSA_PROD_S3_TRAINING_KEY"} | jev.keys()
    )
    assert flags.keys() - jev.keys() <= PRODUCTION.required_keys | PRODUCTION.optional_keys
    assert {key: flags[key] for key in jev} == jev
    assert "CSA_STAGING_CUSTOMER_DECIDE_MODE_DEFAULT" not in flags
    assert flags == {
        "CSA_STAGING_WORKSPACE_ASSISTANT": "true",
        "CSA_STAGING_GATEWAY_MODEL_SECRET": "staging_model_key",
        "CSA_STAGING_INVITE_MAIL": "true",
        "CSA_STAGING_EMAIL_ASSISTANT": "true",
        "CSA_STAGING_BOOKINGS_GOOGLE_CALENDAR": "true",
        "CSA_STAGING_BOOKINGS_GOOGLE_MEET": "true",
        "CSA_STAGING_TRAINING_VIDEO": "true",
        "CSA_PROD_S3_TRAINING_KEY": "prod_s3_training",
        **jev,
    }
    text = path.read_text(encoding="utf-8")
    assert not any(marker in text for marker in ("http", "://", ".fourstacks"))


# --- the optional Jev switches, for every target -----------------------------------------------


VIDEO_KEYS = (
    "CSA_STAGING_TRAINING_VIDEO=",
    "CSA_PROD_S3_TRAINING_KEY=",
    # The Jev lines too: the checked-in file may wire Jev, and a test adds its own.
    "CSA_STAGING_JEV_",
    "CSA_STAGING_ASSISTANT_DECIDER_",
    "CSA_STAGING_CUSTOMER_DECIDE_MODE_DEFAULT=",
)


def without_video_lines(text: str) -> str:
    """The switch file minus its training-video and Jev lines, so a test can add its own."""
    return "".join(
        line for line in text.splitlines(keepends=True) if not line.startswith(VIDEO_KEYS)
    )


def production_flags_with(tmp_path: Path, *lines: str) -> Path:
    """The real production switch file, without its video and Jev lines, plus ``lines``."""
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


def _production_secret_paths(tmp_path: Path, *lines: str) -> tuple[Path, dict[str, Path]]:
    """Secret name to file, as production's plan reads it from the rendered compose config."""
    secret_dir = _secret_dir(tmp_path)
    flags = sr.load_flags(production_flags_with(tmp_path, *lines), PRODUCTION)
    env = sr.build_env(
        flags,
        {"python": "p@sha256:aa", "frontend": "f@sha256:bb"},
        docker_config=tmp_path / "dc",
        secret_dir=secret_dir,
        target=PRODUCTION,
        host_values=HOST_REQUIRED,
    )
    env["HOME"] = str(tmp_path)
    return secret_dir, sr.secret_paths(
        sr.SubprocessRunner(cwd=sr.REPO_ROOT), env, target=PRODUCTION
    )


def test_production_jev_switch_makes_plan_ask_for_the_real_key_file(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable")
    # Wired, with the key file not written yet: plan stops with the file listed as missing.
    wired = (
        "CSA_STAGING_JEV_SECRET=staging_jev_key",
        "CSA_STAGING_ASSISTANT_DECIDER_EMAIL=shadow",
    )
    secret_dir, paths = _production_secret_paths(tmp_path, *wired)
    assert paths["staging_jev_key"] == secret_dir / "pipeline/staging_jev_key"
    # On the instance the same name resolves under the production secret directory.
    assert PRODUCTION.secret_dir / "pipeline/staging_jev_key" == Path(
        "/srv/csa-prod/secrets/pipeline/staging_jev_key"
    )
    assert "staging_disabled_jev_key" not in paths
    assert "staging_jev_key" in sr.missing_secrets(paths)
    paths["staging_jev_key"].write_text("SYNTHETIC-ONLY")
    assert "staging_jev_key" not in sr.missing_secrets(paths)
    # Unwired, nothing asks for it.
    other = tmp_path / "unwired"
    other.mkdir()
    _, unwired = _production_secret_paths(other)
    assert "staging_jev_key" not in unwired
    assert not [name for name in sr.missing_secrets(unwired) if "jev" in name]


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


def test_staging_plan_warns_on_a_downgrade(
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
    warning = (
        f"WARNING: {SHA[:7]} is older than the running {PREV_SHA[:7]}; this is a downgrade. "
        "A downgrade across a migration is refused."
    )
    secrets = make_secret_files(secret_dir, "staging_owner_dsn")
    runner = staging_runner(downgrade=True, secret_files=secrets)
    assert sr.main(["plan"], runner=runner) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines.count(warning) == 1
    # It is a warning only: the apply command is still printed, and the exit stays 0.
    assert lines[-1] == f"{sr.APPLY_COMMAND} --sha {SHA}"
    # A newer commit than the running one prints no warning.
    assert sr.main(["plan"], runner=staging_runner(secret_files=secrets)) == 0
    assert "WARNING" not in capsys.readouterr().out


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
    assert STAGING.optional_keys == {
        "CSA_STAGING_TRAINING_VIDEO",
        "CSA_STAGING_HR_DEFAULT_TEMPLATES",
    }
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
    text = without_video_lines((sr.REPO_ROOT / STAGING.flags_file).read_text(encoding="utf-8"))
    path = tmp_path / "staging-rollout.env"
    path.write_text(text + "CSA_STAGING_TRAINING_VIDEO=false\n", encoding="utf-8")
    assert sr.load_flags(path, STAGING)["CSA_STAGING_TRAINING_VIDEO"] == "false"
    path.write_text(text + "CSA_PROD_S3_TRAINING_KEY=prod_s3_training\n", encoding="utf-8")
    with pytest.raises(sr.RolloutError, match="unknown key CSA_PROD_S3_TRAINING_KEY"):
        sr.load_flags(path, STAGING)


# --- the HR default templates switch ----------------------------------------------------------

HR_DEFAULTS = "CSA_STAGING_HR_DEFAULT_TEMPLATES"


def test_the_hr_default_templates_switch_ships_off_on_both_targets() -> None:
    """Absent is off: the switch is turned on by a reviewed pull request that adds the line, after
    the review of docs/hr/DEFAULT_TEMPLATES_REVIEW.md is signed."""
    for target in (STAGING, PRODUCTION):
        path = sr.REPO_ROOT / target.flags_file
        assert HR_DEFAULTS not in path.read_text(encoding="utf-8"), target.name
        assert HR_DEFAULTS not in sr.load_flags(path, target), target.name
        assert HR_DEFAULTS in target.optional_keys, target.name
        assert target.optional_values[HR_DEFAULTS] == ("false", "true"), target.name
        assert HR_DEFAULTS not in target.host_override_keys | target.host_required_keys


def test_the_hr_default_templates_switch_loads_and_its_value_is_checked(tmp_path: Path) -> None:
    path = production_flags_with(tmp_path, f"{HR_DEFAULTS}=true")
    flags = sr.load_flags(path, PRODUCTION)
    assert flags[HR_DEFAULTS] == "true"
    env = sr.build_env(
        flags,
        {"python": "p@sha256:aa", "frontend": "f@sha256:bb"},
        docker_config=tmp_path / "dc",
        target=PRODUCTION,
    )
    assert env[HR_DEFAULTS] == "true"
    staging = tmp_path / "staging-rollout.env"
    text = (sr.REPO_ROOT / STAGING.flags_file).read_text(encoding="utf-8")
    staging.write_text(text + f"{HR_DEFAULTS}=false\n", encoding="utf-8")
    assert sr.load_flags(staging, STAGING)[HR_DEFAULTS] == "false"
    path = production_flags_with(tmp_path, f"{HR_DEFAULTS}=yes")
    with pytest.raises(sr.RolloutError) as caught:
        sr.load_flags(path, PRODUCTION)
    assert caught.value.message.endswith(f"{HR_DEFAULTS} must be one of false, true")
