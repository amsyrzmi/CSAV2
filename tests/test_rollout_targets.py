from __future__ import annotations

from pathlib import Path

import pytest
import scripts.staging_rollout as sr
from scripts.rollout_targets import STAGING, get_target


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
