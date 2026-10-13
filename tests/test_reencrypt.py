"""Guarded credential re-encrypt skeleton tests (LG01 §6).

Covers:

* the top-of-file guards (missing acknowledgement, prod without confirmation);
* the migration runner NEVER applies the ``.py`` file (it never enters
  ``schema_migrations``) — a pure unit assertion on the discovery glob;
* the cross-boundary crypto test: a token encrypted through the MIGRATION code
  path (``0130``'s ``encrypt_cell`` writing to a testcontainers DB) decrypts
  through the ADAPTER code path, both building AAD via ``credential_aad`` — the
  test that catches a silent cross-path AAD divergence.
"""  # LG01

from __future__ import annotations

import base64
import importlib.util
import uuid
from pathlib import Path
from types import ModuleType

import psycopg
import pytest
from csa.core.crypto import CryptoError, credential_aad, decrypt

from tests.conftest import owner_dsn_for, requires_docker

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION_PY = _REPO_ROOT / "db" / "migrations" / "0130_reencrypt_credentials.py"

# A non-secret base64 of exactly 32 bytes for the crypto path.
_TEST_KEY = "base64:" + base64.b64encode(b"\x04" * 32).decode("ascii")

CLIENT_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _load_migration() -> ModuleType:
    """Load the ``0130`` skeleton by path (its name is not import-safe)."""
    spec = importlib.util.spec_from_file_location("reencrypt_0130", _MIGRATION_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def crypto_env(monkeypatch: pytest.MonkeyPatch):
    """Provide a full, valid Settings env and clear the settings cache."""
    from csa.core import settings

    monkeypatch.setenv("ENV", "ci")
    monkeypatch.setenv("DATABASE_URL", "postgresql://csa_app:pw@localhost:5433/csa")
    monkeypatch.setenv("LG_DATABASE_URL", "postgresql://csa_app:pw@localhost:5433/csa")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setenv("CREDENTIALS_KEY", _TEST_KEY)
    settings.get_settings.cache_clear()
    yield
    settings.get_settings.cache_clear()


def test_migrate_runner_never_applies_the_py_file() -> None:
    """The .py skeleton is not globbed by the migration runner (unit)."""
    from scripts import migrate

    versions = {version for version, _ in migrate.discover_migrations()}
    paths = {path.name for _, path in migrate.discover_migrations()}
    assert "0130_reencrypt_credentials.py" not in paths
    # 0130 has no .sql, so the version is absent from the ledger-bound set.
    assert "0130" not in versions
    assert all(p.endswith(".sql") for p in paths)


def test_guard_blocks_without_acknowledgement() -> None:
    module = _load_migration()
    rc = module.main([])
    assert rc == 2  # guard refusal


def test_guard_blocks_prod_without_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_migration()
    monkeypatch.setenv("ENV", "prod")
    rc = module.main(["--i-understand-this-writes-credentials"])
    assert rc == 2


def test_cross_path_aad_roundtrip_unit(crypto_env: None) -> None:
    """encrypt via migration helper, decrypt via adapter helper (no DB)."""
    module = _load_migration()
    row_id = str(uuid.uuid4())
    token = module.encrypt_cell("access_token", row_id, "s3cr3t")
    assert token.startswith("v1:")
    aad = credential_aad("channel_credentials", "access_token", row_id)
    assert decrypt(token, aad) == "s3cr3t"
    # AAD binds the column: a mismatched column must fail.
    with pytest.raises(CryptoError):
        decrypt(token, credential_aad("channel_credentials", "refresh_token", row_id))


@requires_docker
@pytest.mark.asyncio
async def test_cross_boundary_encrypt_write_then_adapter_decrypt(
    migrated_dsn: str, pg_container: object, crypto_env: None
) -> None:
    """Migration path encrypts + writes a row; adapter path decrypts it.

    Proves the two AAD constructions are byte-identical across the boundary.
    """
    module = _load_migration()
    owner_dsn = owner_dsn_for(pg_container)
    cred_id = str(uuid.uuid4())

    # Seed a plaintext credential row as owner csa.
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version) "
            "VALUES (%s, 'reenc', 'reenc', 'lg01-test') ON CONFLICT (id) DO NOTHING",
            (CLIENT_ID,),
        )
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel, access_token, "
            "refresh_token) VALUES (%s, %s, 'webhook', %s, %s)",
            (cred_id, CLIENT_ID, "plain-access", "plain-refresh"),
        )

    # Run the migration encrypt-and-write path.
    written = await module.reencrypt(owner_dsn)
    assert written >= 2  # both columns of our row

    # Re-run is idempotent (rows already v1: are skipped) -> 0 new writes.
    assert await module.reencrypt(owner_dsn) == 0

    # Read back the stored v1: ciphertext and decrypt via the ADAPTER path.
    with psycopg.connect(owner_dsn) as conn:
        row = conn.execute(
            "SELECT access_token, refresh_token FROM channel_credentials WHERE id = %s",
            (cred_id,),
        ).fetchone()
    assert row is not None
    access_ct, refresh_ct = row
    assert access_ct.startswith("v1:")
    assert refresh_ct.startswith("v1:")

    assert (
        decrypt(access_ct, credential_aad("channel_credentials", "access_token", cred_id))
        == "plain-access"
    )
    assert (
        decrypt(refresh_ct, credential_aad("channel_credentials", "refresh_token", cred_id))
        == "plain-refresh"
    )
