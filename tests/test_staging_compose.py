"""Synthetic, non-starting checks for the local staging Compose proposal."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "deploy" / "compose.staging-local-proposal.yml"
SYNTHETIC_VALUE = "SYNTHETIC-ONLY-NOT-A-CREDENTIAL"
SECRET_FILES = {
    "database/staging_owner_password",
    "migration/staging_owner_dsn",
    "migration/staging_app_password",
    "migration/staging_ro_password",
    "migration/staging_identity_password",
    "runtime/staging_domain_dsn",
    "runtime/staging_checkpoint_dsn",
    "runtime/staging_identity_dsn",
    "runtime/staging_credentials_key",
    "runtime/staging_disabled_model_key",
}


def _compose_env(secret_dir: Path) -> dict[str, str]:
    return {
        "HOME": str(secret_dir),
        "PATH": os.defpath,
        "CSA_STAGING_PROJECT": "csa-staging-synthetic",
        "CSA_STAGING_PYTHON_IMAGE": f"example.invalid/csa-python@sha256:{'0' * 64}",
        "CSA_STAGING_FRONTEND_IMAGE": f"example.invalid/csa-frontend@sha256:{'1' * 64}",
        "CSA_STAGING_CONSOLE_ORIGIN": "https://staging.invalid",
        "CSA_STAGING_SECRET_DIR": str(secret_dir),
    }


def _make_synthetic_secrets(root: Path) -> None:
    for relative_path in SECRET_FILES:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(SYNTHETIC_VALUE, encoding="utf-8")
        path.chmod(0o400)


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI is unavailable")
def test_local_staging_compose_is_valid_and_network_isolated(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "synthetic-staging-secrets"
    secrets_dir.mkdir(mode=0o700)
    _make_synthetic_secrets(secrets_dir)

    result = subprocess.run(
        [
            "docker",
            "compose",
            "--file",
            str(COMPOSE_FILE),
            "--profile",
            "staging-approved",
            "--profile",
            "migration-approved",
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_compose_env(secrets_dir),
    )
    rendered = json.loads(result.stdout)

    assert set(rendered["services"]) == {"staging-db", "gateway", "frontend", "migrate"}
    assert {
        service_name: set(service["profiles"])
        for service_name, service in rendered["services"].items()
    } == {
        "staging-db": {"staging-approved", "migration-approved"},
        "gateway": {"staging-approved"},
        "frontend": {"staging-approved"},
        "migrate": {"migration-approved"},
    }
    assert all("ports" not in service for service in rendered["services"].values())
    assert "cloudflared" not in rendered["services"]
    assert set(rendered["services"]["staging-db"]["networks"]) == {"data"}
    assert set(rendered["services"]["gateway"]["networks"]) == {"data", "app"}
    assert set(rendered["services"]["frontend"]["networks"]) == {"app", "edge"}
    assert set(rendered["services"]["migrate"]["networks"]) == {"data"}
    assert set(rendered["networks"]) == {"data", "app", "edge"}
    assert all(network["internal"] for network in rendered["networks"].values())
    assert "secrets" not in rendered["services"]["frontend"]

    grants = {
        service_name: {
            item if isinstance(item, str) else item["source"] for item in service.get("secrets", [])
        }
        for service_name, service in rendered["services"].items()
    }
    assert grants == {
        "staging-db": {"staging_owner_password"},
        "gateway": {
            "staging_domain_dsn",
            "staging_checkpoint_dsn",
            "staging_identity_dsn",
            "staging_credentials_key",
            "staging_disabled_model_key",
        },
        "frontend": set(),
        "migrate": {
            "staging_owner_dsn",
            "staging_app_password",
            "staging_ro_password",
            "staging_identity_password",
        },
    }
    secret_sources = {secret["file"] for secret in rendered["secrets"].values()}
    assert len(secret_sources) == len(SECRET_FILES)
    assert all(Path(source).is_absolute() for source in secret_sources)
    assert all(Path(source).is_file() for source in secret_sources)
    assert all(stat.S_IMODE(Path(source).stat().st_mode) == 0o400 for source in secret_sources)
    assert SYNTHETIC_VALUE not in result.stdout


@pytest.mark.parametrize(
    ("file_uid", "file_mode", "consumer_uid", "expected"),
    [
        (1000, 0o400, 1000, True),
        (1000, 0o400, 999, False),
        (999, 0o400, 999, True),
        (999, 0o400, 1000, False),
    ],
)
def test_secret_file_owner_mode_grants_only_the_intended_uid(
    file_uid: int, file_mode: int, consumer_uid: int, expected: bool
) -> None:
    owner_read = bool(file_mode & stat.S_IRUSR)
    other_read = bool(file_mode & stat.S_IROTH)
    readable = owner_read if consumer_uid == file_uid else other_read

    assert readable is expected
