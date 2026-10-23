"""Staging pipeline wiring, secret handling and synthetic bot registration."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from csa.core import crypto, db
from pydantic import SecretStr
from scripts import staging_pipeline_key, staging_secrets, staging_test_bot

from tests.conftest import owner_dsn_for, requires_docker
from tests.test_staging_compose import _compose_env, _make_synthetic_secrets

ROOT = Path(__file__).resolve().parents[1]
TOKEN = SecretStr("123456:synthetic-development-token")


def render(tmp_path: Path, *profiles: str) -> dict:
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir(mode=0o700)
    _make_synthetic_secrets(secret_dir)
    (secret_dir / "pipeline").mkdir(mode=0o700)
    (secret_dir / "pipeline/staging_model_key").write_text("SYNTHETIC-ONLY")
    command = ["docker", "compose", "--env-file", "/dev/null"]
    for name in (
        "compose.staging-local-proposal.yml",
        "compose.staging-shared-caddy.yml",
        "compose.staging-pipeline.yml",
    ):
        command += ["--file", str(ROOT / "deploy" / name)]
    for profile in profiles:
        command += ["--profile", profile]
    command += ["config", "--format", "json"]
    result = subprocess.run(
        command, env=_compose_env(secret_dir), capture_output=True, text=True, check=True
    )
    assert "SYNTHETIC-ONLY" not in result.stdout
    return json.loads(result.stdout)


def test_pipeline_connects_consumers_to_same_data_and_storage(tmp_path: Path) -> None:
    config = render(tmp_path, "staging-approved", "pipeline-approved")
    services = config["services"]
    assert set(services) == {
        "staging-db",
        "frontend",
        "gateway",
        "worker",
        "delivery",
        "staging-embeddings",
    }
    assert services["worker"]["command"] == ["python", "-m", "apps.worker.main"]
    assert services["delivery"]["command"] == ["python", "-m", "apps.delivery.main"]
    assert services["worker"]["environment"]["KB_INGEST_ENABLED"] == "true"
    assert services["gateway"]["environment"]["KB_INGEST_ENABLED"] == "false"
    for name in ("gateway", "worker", "delivery"):
        assert "data" in services[name]["networks"]
        targets = {s["target"]: s["source"] for s in services[name]["secrets"]}
        assert targets["DATABASE_URL"] == "staging_domain_dsn"
        assert targets["LG_DATABASE_URL"] == "staging_checkpoint_dsn"
        assert "provider-egress" in services[name]["networks"]
    worker_secrets = {s["target"]: s["source"] for s in services["worker"]["secrets"]}
    assert worker_secrets["GROQ_API_KEY"] == "staging_model_key"
    assert "IDENTITY_DATABASE_URL" not in worker_secrets
    assert "staging_owner_dsn" not in worker_secrets.values()
    for name in ("gateway", "worker"):
        env = services[name]["environment"]
        assert env["TEI_ENDPOINT"] == "http://staging-embeddings:8080"
        assert env["KB_EMBED_MODEL_LABEL"] == "multilingual-e5-small-fd1525a"
        assert any(v["source"] == "staging-uploads" for v in services[name]["volumes"])
    assert services["staging-embeddings"]["image"].endswith(
        "@sha256:cc1f06b5b2e38d979aca1cc054675426bf4d94247f6d22e399d73e09131dfe79"
    )
    assert not services["staging-embeddings"].get("secrets")
    assert set(services["staging-embeddings"]["networks"]) == {"embeddings", "model-egress"}
    assert config["networks"]["data"]["internal"]
    assert config["networks"]["embeddings"]["internal"]
    for name, service in services.items():
        for port in service.get("ports", []):
            assert port["host_ip"] == "127.0.0.1"
            assert (name, str(port["published"])) in {("gateway", "18081"), ("frontend", "18030")}
    assert "/tmp/worker.alive" in services["worker"]["healthcheck"]["test"][-1]


def test_pipeline_keeps_one_shot_and_migration_services_out_of_normal_startup(
    tmp_path: Path,
) -> None:
    config = render(tmp_path, "staging-approved")
    assert set(config["services"]) == {"staging-db", "gateway", "frontend"}


def test_pipeline_setup_mounts_only_operator_code_and_expected_secrets(tmp_path: Path) -> None:
    config = render(
        tmp_path, "staging-approved", "test-bot-setup-approved", "pipeline-bootstrap-approved"
    )
    setup = config["services"]["staging-bot-setup"]
    assert all(v["read_only"] for v in setup["volumes"])
    assert {v["target"] for v in setup["volumes"]} == {
        "/app/scripts/staging_test_bot.py",
        "/app/db/sql/staging",
    }
    assert config["services"]["embeddings-init"]["network_mode"] == "none"
    assert config["services"]["embeddings-init"]["cap_add"] == ["CHOWN"]


def test_pipeline_key_is_private_and_never_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "secrets"
    staging_secrets.create(target, os.getuid())
    value = SecretStr("synthetic-staging-provider-value")
    staging_pipeline_key.provision(target, value)
    key = target / "pipeline/staging_model_key"
    assert stat.S_IMODE(key.stat().st_mode) == 0o400
    assert key.read_text() == value.get_secret_value()
    assert staging_secrets.check(target, os.getuid()) == []
    with pytest.raises(FileExistsError):
        staging_pipeline_key.provision(target, SecretStr("another-synthetic-value"))
    assert key.read_text() == value.get_secret_value()


@pytest.mark.parametrize(
    "value", ["", "short", staging_secrets.DISABLED_MODEL_KEY, "x" * 25 + "\n"]
)
def test_pipeline_key_rejects_placeholder_or_malformed_input(tmp_path: Path, value: str) -> None:
    target = tmp_path / "secrets"
    staging_secrets.create(target, os.getuid())
    with pytest.raises(staging_secrets.ProvisionError):
        staging_pipeline_key.provision(target, SecretStr(value))
    assert not (target / "pipeline/staging_model_key").exists()


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://csa:synthetic@postgres:5432/csa",
        "postgresql://csa:synthetic@127.0.0.1:5433/csa_staging",
        "postgresql://csa_app:synthetic@staging-db:5432/csa_staging",
        "postgresql://csa:synthetic@staging-db:5432/csa_staging?host=postgres",
        "postgresql://csa:synthetic@staging-db:bad/csa_staging",
    ],
)
def test_test_bot_refuses_non_staging_targets(dsn: str) -> None:
    with pytest.raises(staging_test_bot.SetupError):
        staging_test_bot.validate_target(dsn)


def test_test_bot_accepts_exact_staging_owner() -> None:
    staging_test_bot.validate_target("postgresql://csa:synthetic@staging-db:5432/csa_staging")


async def test_telegram_errors_do_not_expose_token() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": TOKEN.get_secret_value()})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(staging_test_bot.SetupError) as caught:
            await staging_test_bot.telegram(client, TOKEN, "getMe", {})
    assert TOKEN.get_secret_value() not in str(caught.value)


@requires_docker
async def test_bot_binding_is_atomic_encrypted_and_cannot_move_tenants(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(crypto, "_key", lambda: b"\x05" * 32)
    first, other = uuid4(), uuid4()
    names = (f"staging-{first}", f"staging-{other}")
    with psycopg.connect(owner_dsn_for(pg_container)) as conn:
        for tenant_id, name in zip((first, other), names, strict=True):
            conn.execute(
                "INSERT INTO clients(id,slug,name,status,platform_version,config) "
                "VALUES(%s,%s,%s,'pilot','langgraph',%s)",
                (tenant_id, name, name, psycopg.types.json.Jsonb({"runtime": "langgraph"})),
            )
    pool = db.build_pool(owner_dsn_for(pg_container), max_size=3)
    await pool.open(wait=True)
    bot = staging_test_bot.Bot(id=123456, is_bot=True)
    try:
        a, b = await asyncio.gather(
            staging_test_bot.bind_bot(pool, names[0], bot, TOKEN),
            staging_test_bot.bind_bot(pool, names[0], bot, TOKEN),
        )
        assert a == b and a.client_id == first
        with pytest.raises(staging_test_bot.SetupError, match="tenant binding"):
            await staging_test_bot.bind_bot(pool, names[1], bot, TOKEN)
        with pytest.raises(staging_test_bot.SetupError, match="token differs"):
            await staging_test_bot.bind_bot(
                pool, names[0], bot, SecretStr("changed-synthetic-token")
            )
        with psycopg.connect(owner_dsn_for(pg_container)) as conn:
            row = conn.execute(
                "SELECT id,access_token,meta FROM channel_credentials WHERE client_id=%s", (first,)
            ).fetchone()
            assert row is not None
            assert row[1] != TOKEN.get_secret_value()
            assert (
                crypto.decrypt(
                    row[1],
                    crypto.credential_aad("channel_credentials", "access_token", str(row[0])),
                )
                == TOKEN.get_secret_value()
            )
            assert conn.execute(
                "SELECT count(*) FROM console_audit WHERE client_id=%s", (first,)
            ).fetchone() == (1,)
            assert conn.execute(
                "SELECT count(*) FROM channel_credentials WHERE client_id=%s", (other,)
            ).fetchone() == (0,)
        original = db.q.execute

        async def fail_audit(path: str, *args: object, **kwargs: object) -> object:
            if path == "console/audit.sql":
                raise RuntimeError("synthetic audit failure")
            return await original(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(db.q, "execute", fail_audit)
        with pytest.raises(RuntimeError, match="synthetic audit"):
            await staging_test_bot.bind_bot(
                pool, names[1], staging_test_bot.Bot(id=654321, is_bot=True), TOKEN
            )
        with psycopg.connect(owner_dsn_for(pg_container)) as conn:
            assert conn.execute(
                "SELECT count(*) FROM channel_credentials WHERE client_id=%s", (other,)
            ).fetchone() == (0,)
    finally:
        await pool.close()
