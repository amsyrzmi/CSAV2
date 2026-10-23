"""Synthetic, non-starting checks for the local staging Compose proposal."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
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
    "connector/staging_tunnel_token",
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
            "--profile",
            "admin-bootstrap-approved",
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

    assert set(rendered["services"]) == {
        "staging-db",
        "gateway",
        "frontend",
        "cloudflared",
        "migrate",
        "uploads-init",
        "admin-bootstrap",
    }
    assert {
        service_name: set(service["profiles"])
        for service_name, service in rendered["services"].items()
    } == {
        "staging-db": {"staging-approved", "migration-approved"},
        "gateway": {"staging-approved"},
        "frontend": {"staging-approved"},
        "cloudflared": {"staging-approved"},
        "migrate": {"migration-approved"},
        "uploads-init": {"migration-approved"},
        "admin-bootstrap": {"admin-bootstrap-approved"},
    }
    assert all(service["profiles"] for service in rendered["services"].values())
    assert all("ports" not in service for service in rendered["services"].values())
    assert set(rendered["services"]["staging-db"]["networks"]) == {"data"}
    assert set(rendered["services"]["gateway"]["networks"]) == {"data", "app"}
    assert set(rendered["services"]["frontend"]["networks"]) == {"app", "edge"}
    connector = rendered["services"]["cloudflared"]
    assert set(connector["networks"]) == {"edge", "connector-egress"}
    assert set(rendered["services"]["migrate"]["networks"]) == {"data"}
    assert set(rendered["services"]["admin-bootstrap"]["networks"]) == {"data"}
    uploads_init = rendered["services"]["uploads-init"]
    assert uploads_init["network_mode"] == "none"
    assert "networks" not in uploads_init
    assert set(rendered["networks"]) == {"data", "app", "edge", "connector-egress"}
    assert all(
        network["internal"]
        for name, network in rendered["networks"].items()
        if name != "connector-egress"
    )
    assert not rendered["networks"]["connector-egress"].get("internal", False)
    assert not rendered["networks"]["connector-egress"].get("external", False)
    assert {
        service_name
        for service_name, service in rendered["services"].items()
        if "connector-egress" in service.get("networks", {})
    } == {"cloudflared"}
    assert all(not network.get("external", False) for network in rendered["networks"].values())
    assert "secrets" not in rendered["services"]["frontend"]
    assert connector["image"] == (
        "cloudflare/cloudflared:2026.10.3@sha256:"
        "faa8e0a1475f137c910be6025f43c8e4092fdd35461b31e19fd1390657a1ff39"
    )
    assert connector["user"] == "65532:65532"
    assert connector["read_only"] is True
    assert connector["cap_drop"] == ["ALL"]
    assert connector["security_opt"] == ["no-new-privileges:true"]
    assert connector["command"] == ["tunnel", "run"]
    assert connector["environment"]["TUNNEL_TOKEN_FILE"] == ("/run/secrets/staging_tunnel_token")
    assert "TUNNEL_TOKEN" not in connector["environment"]
    assert connector.get("network_mode") != "host"
    assert not connector.get("privileged", False)
    assert not connector.get("volumes")

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
        "cloudflared": {"staging_tunnel_token"},
        "migrate": {
            "staging_owner_dsn",
            "staging_app_password",
            "staging_ro_password",
            "staging_identity_password",
        },
        "uploads-init": set(),
        "admin-bootstrap": {"staging_identity_dsn"},
    }
    assert all(
        "staging_tunnel_token" not in service_grants
        for service_name, service_grants in grants.items()
        if service_name != "cloudflared"
    )
    secret_sources = {secret["file"] for secret in rendered["secrets"].values()}
    assert len(secret_sources) == len(SECRET_FILES)
    assert all(Path(source).is_absolute() for source in secret_sources)
    assert all(Path(source).is_file() for source in secret_sources)
    assert all(stat.S_IMODE(Path(source).stat().st_mode) == 0o400 for source in secret_sources)
    connector_secret_file = rendered["secrets"]["staging_tunnel_token"]["file"]
    assert connector_secret_file == str(secrets_dir / "connector/staging_tunnel_token")
    assert stat.S_IMODE((secrets_dir / "connector").stat().st_mode) == 0o700
    assert SYNTHETIC_VALUE not in result.stdout

    assert uploads_init["image"] == _compose_env(secrets_dir)["CSA_STAGING_PYTHON_IMAGE"]
    assert uploads_init["user"] == "0:0"
    assert uploads_init["read_only"] is True
    assert uploads_init["cap_drop"] == ["ALL"]
    assert uploads_init["cap_add"] == ["CHOWN"]
    assert uploads_init["security_opt"] == ["no-new-privileges:true"]
    assert uploads_init["entrypoint"] == ["chown", "1000:1000", "/var/lib/csa/uploads"]
    assert [volume["source"] for volume in uploads_init["volumes"]] == ["staging-uploads"]
    assert uploads_init["healthcheck"]["disable"] is True
    assert "depends_on" not in uploads_init

    admin = rendered["services"]["admin-bootstrap"]
    assert admin["user"] == "1000:1000"
    assert admin["read_only"] is True
    assert admin["cap_drop"] == ["ALL"]
    assert admin["entrypoint"][:2] == ["python", "-c"]
    assert 'run_module("scripts.bootstrap_console_admin"' in admin["entrypoint"][2]
    assert admin["environment"]["CSA_IDENTITY_DSN_FILE"] == "/run/secrets/IDENTITY_DATABASE_URL"
    assert admin["secrets"] == [
        {"source": "staging_identity_dsn", "target": "IDENTITY_DATABASE_URL"}
    ]
    assert admin["environment"]["CSA_ADMIN_BOOTSTRAP_APPROVAL"] == "disabled"
    assert rendered["services"]["migrate"]["environment"]["CSA_MIGRATION_APPROVAL"] == "disabled"
    assert not admin.get("volumes")


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI is unavailable")
def test_private_startup_selection_without_connector_secret(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "synthetic-staging-secrets"
    secrets_dir.mkdir(mode=0o700)
    _make_synthetic_secrets(secrets_dir)
    (secrets_dir / "connector/staging_tunnel_token").unlink()
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "--project-name",
            "csa-staging-synthetic",
            "--file",
            str(COMPOSE_FILE),
            "--profile",
            "staging-approved",
            "config",
            "--format",
            "json",
            "staging-db",
            "gateway",
            "frontend",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**_compose_env(secrets_dir), "COMPOSE_PROFILES": ""},
    )
    rendered = json.loads(result.stdout)
    services = rendered["services"]
    assert set(services) == {"staging-db", "gateway", "frontend"}
    for name in ("gateway", "frontend"):
        service = services[name]
        assert (
            service["image"]
            == _compose_env(secrets_dir)[
                f"CSA_STAGING_{'PYTHON' if name == 'gateway' else 'FRONTEND'}_IMAGE"
            ]
        )
        assert service["user"] == "1000:1000"
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert not service.get("ports")
        assert not service.get("privileged", False)
        assert "connector-egress" not in service["networks"]
    gateway = services["gateway"]
    assert gateway["environment"]["KB_INGEST_ENABLED"] == "false"
    assert gateway["environment"]["NO_LOCAL_ENV"] == "1"
    assert gateway["environment"]["ENV"] == "prod"
    assert gateway["environment"]["CONSOLE_COOKIE_SECURE"] == "true"
    assert gateway["depends_on"]["staging-db"]["condition"] == "service_healthy"
    assert services["frontend"]["depends_on"]["gateway"]["condition"] == "service_healthy"
    assert services["frontend"]["environment"]["CSA_CONSOLE_API_ORIGIN"] == "http://gateway:8080"
    assert not services["frontend"].get("secrets")
    assert {secret["target"] for secret in gateway["secrets"]} == {
        "DATABASE_URL",
        "LG_DATABASE_URL",
        "IDENTITY_DATABASE_URL",
        "CREDENTIALS_KEY",
        "GROQ_API_KEY",
    }
    assert SYNTHETIC_VALUE not in result.stdout


def _render_admin_gate(tmp_path: Path) -> str:
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
            "admin-bootstrap-approved",
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_compose_env(secrets_dir),
    )
    entrypoint = json.loads(result.stdout)["services"]["admin-bootstrap"]["entrypoint"]
    assert entrypoint[:2] == ["python", "-c"]
    return entrypoint[2]


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI is unavailable")
@pytest.mark.parametrize(
    ("approval", "dsn", "delegated"),
    [
        (
            "approved-isolated-staging",
            "postgresql://csa_identity@staging-db:5432/csa_staging",
            True,
        ),
        ("approved-isolated-staging", "postgres://csa_identity@staging-db/csa_staging", True),
        (None, "postgresql://csa_identity@staging-db/csa_staging", False),
        ("disabled", "postgresql://csa_identity@staging-db/csa_staging", False),
        ("approved-isolated-staging", "postgresql://csa@staging-db/csa_staging", False),
        ("approved-isolated-staging", "postgresql://csa_app@staging-db/csa_staging", False),
        ("approved-isolated-staging", "postgresql://csa_identity@postgres/csa_staging", False),
        ("approved-isolated-staging", "postgresql://csa_identity@staging-db/csa", False),
        (
            "approved-isolated-staging",
            "postgresql://csa_identity@staging-db:5434/csa_staging",
            False,
        ),
        (
            "approved-isolated-staging",
            "postgresql://csa_identity@staging-db/csa_staging?host=postgres",
            False,
        ),
    ],
)
def test_admin_bootstrap_gate_requires_approval_and_identity_target(
    tmp_path: Path, approval: str | None, dsn: str, *, delegated: bool
) -> None:
    code = _render_admin_gate(tmp_path)
    # A stub stands in for the packaged module; it records what it would receive.
    stub_root = tmp_path / "stub"
    (stub_root / "scripts").mkdir(parents=True)
    (stub_root / "scripts" / "__init__.py").write_text("")
    record = tmp_path / "delegated.json"
    (stub_root / "scripts" / "bootstrap_console_admin.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['STUB_RECORD']).write_text(json.dumps("
        "[os.environ['IDENTITY_DATABASE_URL'], sys.argv[1:]]))\n"
    )
    dsn_file = tmp_path / "IDENTITY_DATABASE_URL"
    dsn_file.write_text(dsn + "\n")
    env = {
        "PATH": os.defpath,
        "CSA_IDENTITY_DSN_FILE": str(dsn_file if approval else tmp_path / "absent"),
        "STUB_RECORD": str(record),
    }
    if approval is not None:
        env["CSA_ADMIN_BOOTSTRAP_APPROVAL"] = approval
    args = ["--email", "admin@example.test", "--create-first-platform-admin"]
    result = subprocess.run(
        [sys.executable, "-E", "-s", "-c", code, *args],
        cwd=stub_root,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert "staging-db" not in result.stdout + result.stderr
    if delegated:
        assert result.returncode == 0, result.stderr
        assert json.loads(record.read_text()) == [dsn, args]
    else:
        assert result.returncode != 0
        assert not record.exists()
        expected = "approval required" if approval != "approved-isolated-staging" else "target"
        assert expected in result.stderr


@pytest.mark.parametrize(
    ("file_uid", "file_mode", "consumer_uid", "expected"),
    [
        (1000, 0o400, 1000, True),
        (1000, 0o400, 999, False),
        (999, 0o400, 999, True),
        (999, 0o400, 1000, False),
        (65532, 0o400, 65532, True),
        (65532, 0o400, 1000, False),
    ],
)
def test_secret_file_owner_mode_grants_only_the_intended_uid(
    file_uid: int, file_mode: int, consumer_uid: int, expected: bool
) -> None:
    owner_read = bool(file_mode & stat.S_IRUSR)
    other_read = bool(file_mode & stat.S_IROTH)
    readable = owner_read if consumer_uid == file_uid else other_read

    assert readable is expected
