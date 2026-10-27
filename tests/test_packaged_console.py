"""Synthetic packaged-console acceptance; never use persistent Compose or providers."""

from __future__ import annotations

import ipaddress
import json
import re
import shutil
import ssl
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import LiteralString
from urllib.parse import urlsplit
from uuid import uuid4

import docker
import httpx
import psycopg
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from docker.models.containers import Container
from playwright.async_api import BrowserContext, Locator, Page, Request, Route, async_playwright
from psycopg import sql
from scripts.release_context import materialize
from testcontainers.core.network import Network

from tests.conftest import (
    CSA_APP_PASSWORD,
    REPO_ROOT,
    owner_dsn_for,
    requires_docker,
    reset_console_identity,
)

pytestmark = [requires_docker, pytest.mark.asyncio, pytest.mark.slow]
PASSWORD = "synthetic-packaged-initial-password"
CHANGED_PASSWORD = "synthetic-packaged-changed-password"
IDENTITY_PASSWORD = "synthetic_packaged_identity_pw"


@contextmanager
def tls_frontend(upstream: str, directory: Path) -> Iterator[tuple[str, ssl.SSLContext]]:
    """A test-only TLS terminator; the deployed proxy/hostname remain unverified."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "Packaged synthetic TLS")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "synthetic.pem", directory / "synthetic-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(cert_path, key_path)

    class Proxy(BaseHTTPRequestHandler):
        def relay(self) -> None:
            excluded = {"connection", "transfer-encoding", "content-length", "content-encoding"}
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            headers = {k: v for k, v in self.headers.items() if k.lower() not in excluded}
            headers["Accept-Encoding"] = "identity"
            with httpx.Client(trust_env=False, timeout=30) as client:
                response = client.request(
                    self.command, upstream + self.path, headers=headers, content=body
                )
            try:
                self.send_response(response.status_code)
                for header, value in response.headers.multi_items():
                    if header.lower() not in excluded:
                        self.send_header(header, value)
                self.send_header("Content-Length", str(len(response.content)))
                self.end_headers()
                self.wfile.write(response.content)
            except BrokenPipeError:
                return

        do_GET = do_POST = do_PATCH = do_DELETE = relay

        def log_message(self, format: str, *args: object) -> None:
            # Do not log identity tokens, cookies or request URLs.
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Proxy) as server:
        server.socket = server_tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield (
                f"https://127.0.0.1:{server.server_port}",
                ssl.create_default_context(cafile=str(cert_path)),
            )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()
            key_path.unlink()
            cert_path.unlink()


def await_http(url: str, container: Container) -> None:
    deadline = time.monotonic() + 60
    with httpx.Client(trust_env=False, timeout=2) as client:
        while time.monotonic() < deadline:
            container.reload()
            assert container.status == "running", container.logs().decode()
            try:
                response = client.get(url)
            except httpx.TransportError:
                time.sleep(0.1)
                continue
            if response.status_code == 200:
                return
            time.sleep(0.1)
    pytest.fail(f"Packaged service did not become available: {url}")


@pytest.fixture(scope="module")
def packaged_images(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    node = shutil.which("node")
    assert node, "Node is required to materialize the reviewed frontend context"
    root = tmp_path_factory.mktemp("packaged-contexts")
    python_context, frontend_context = root / "python", root / "frontend"
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
    ).strip()
    timestamp = subprocess.check_output(
        ["git", "log", "-1", "--format=%ct"], cwd=REPO_ROOT, text=True
    ).strip()
    with closing(docker.from_env()) as client, ExitStack() as cleanup:
        cleanup.callback(shutil.rmtree, root)
        materialize(REPO_ROOT, python_context)
        subprocess.run(
            [
                node,
                str(REPO_ROOT / "CSA_WebConsole/csa-console/web/scripts/check-isolated.mjs"),
                "--context",
                str(frontend_context),
            ],
            check=True,
            cwd=REPO_ROOT,
            env={"PATH": str(Path(node).parent)},
        )
        images = []
        for kind, context in (("python", python_context), ("frontend", frontend_context)):
            tag = f"csa-packaged-acceptance-{kind}:{uuid4().hex}"
            client.images.build(
                path=str(context),
                tag=tag,
                rm=True,
                buildargs={
                    "SOURCE_REVISION": revision,
                    "SOURCE_DATE_EPOCH": timestamp,
                    "CSA_CONSOLE_API_ORIGIN": "http://gateway:8080",
                },
            )
            cleanup.callback(client.images.remove, tag)
            images.append(tag)
        yield images[0], images[1]


@dataclass
class PackagedConsole:
    origin: str
    tls: ssl.SSLContext
    gateway: str


@pytest.fixture
def packaged_console(
    packaged_images: tuple[str, str],
    packaged_identity: None,
    pg_container: object,
    migrated_dsn: str,
    tmp_path: Path,
) -> Iterator[PackagedConsole]:
    # The network is attached only to this module's disposable Testcontainer.
    assert migrated_dsn == owner_dsn_for(pg_container)
    with (
        closing(docker.from_env()) as client,
        Network(docker_network_kw={"internal": True}) as network,
        ExitStack() as cleanup,
    ):
        db_container = pg_container.get_wrapped_container()  # type: ignore[attr-defined]
        network.connect(db_container.id, ["packaged-db"])
        cleanup.callback(client.networks.get(network.name).disconnect, db_container.id, force=True)

        def start(image: str, port: int, alias: str, env: dict[str, str]) -> tuple[Container, str]:
            container = client.containers.run(
                image,
                detach=True,
                environment=env,
                network=network.name,
                networking_config={
                    network.name: client.api.create_endpoint_config(aliases=[alias])
                },
                read_only=True,
                tmpfs={"/tmp": "", "/app/.next/cache": ""},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
            )
            cleanup.callback(container.remove, force=True, v=True)
            container.reload()
            assert not container.attrs["HostConfig"]["PortBindings"]
            address = container.attrs["NetworkSettings"]["Networks"][network.name]["IPAddress"]
            assert ipaddress.ip_address(address).is_private
            return container, f"http://{address}:{port}"

        frontend, frontend_url = start(
            packaged_images[1],
            3000,
            "frontend",
            {"CSA_CONSOLE_API_ORIGIN": "http://gateway:8080"},
        )
        with tls_frontend(frontend_url, tmp_path) as (origin, tls):
            domain = f"postgresql://csa_app:{CSA_APP_PASSWORD}@packaged-db:5432/csa"
            gateway, gateway_url = start(
                packaged_images[0],
                8080,
                "gateway",
                {
                    "ENV": "prod",
                    "NO_LOCAL_ENV": "1",
                    "DATABASE_URL": domain,
                    "LG_DATABASE_URL": domain,
                    "IDENTITY_DATABASE_URL": f"postgresql://csa_identity:{IDENTITY_PASSWORD}@packaged-db:5432/csa",
                    "CONSOLE_ALLOWED_ORIGIN": origin,
                    "CONSOLE_COOKIE_SECURE": "true",
                    "PUBLIC_BASE_URL": origin,
                    "CREDENTIALS_KEY": "base64:c3ludGhldGljLXBhY2thZ2VkLWZpeHR1cmUta2V5ISE=",
                    "GROQ_API_KEY": "synthetic-disabled-not-a-provider-key",
                    # /auth/me then reports workspace_assistant, so the dock button renders;
                    # no assistant turn is driven against the synthetic provider key.
                    "WORKSPACE_ASSISTANT_ENABLED": "true",
                    "TURN_ENGINE": "coordinator",
                    "CHANNEL_MODE": "webhook",
                    "KB_INGEST_ENABLED": "false",
                },
            )
            await_http(gateway_url + "/readyz", gateway)
            await_http(frontend_url + "/login", frontend)
            yield PackagedConsole(origin, tls, gateway_url)


@pytest.fixture
async def packaged_identity(migrated_dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(migrated_dsn, autocommit=True) as conn:
        # The module shares one disposable database; isolate each identity scenario.
        await reset_console_identity(conn)
        await conn.execute(
            sql.SQL("ALTER ROLE csa_identity LOGIN PASSWORD {}").format(
                sql.Literal(IDENTITY_PASSWORD)
            )
        )
    from csa.console.identity import IdentityConfig, IdentityService
    from csa.core.db import build_pool
    from psycopg.conninfo import make_conninfo

    pool = build_pool(
        make_conninfo(migrated_dsn, user="csa_identity", password=IDENTITY_PASSWORD),
        min_size=1,
        max_size=1,
        autocommit=True,
    )
    await pool.open(wait=True)
    try:
        await IdentityService(
            pool, IdentityConfig(origins=("https://synthetic.example.test",))
        ).bootstrap("packaged-admin@example.test", PASSWORD, "Synthetic Packaged Admin")
    finally:
        await pool.close()


async def test_packaged_identity_readiness_and_boundaries(
    packaged_console: PackagedConsole,
    migrated_dsn: str,
) -> None:
    w = packaged_console
    with ExitStack() as browsers:

        def browser() -> httpx.Client:
            client = browsers.enter_context(
                httpx.Client(
                    base_url=w.origin,
                    verify=w.tls,
                    trust_env=False,
                    timeout=30,
                )
            )
            response = client.get("/console/v1/auth/csrf")
            assert response.status_code == 200
            assert response.json()["csrf_token"] == client.cookies["csa_csrf"]
            assert response.headers["cache-control"] == "no-store"
            assert all(cookie.secure for cookie in client.cookies.jar)
            return client

        def mutate(client: httpx.Client, path: str, body: object) -> httpx.Response:
            return client.post(
                path,
                json=body,
                headers={
                    "Origin": w.origin,
                    "X-CSRF-Token": client.cookies["csa_csrf"],
                },
            )

        anonymous, admin = browser(), browser()
        assert anonymous.get("/console/v1/auth/me").status_code == 401
        assert anonymous.get("/console/v1/business/orders").status_code == 401
        assert anonymous.get("/business").status_code == 307
        login_body = {"email": "packaged-admin@example.test", "password": PASSWORD}
        assert admin.post("/console/v1/auth/login", json=login_body).status_code == 403
        assert (
            admin.post(
                "/console/v1/auth/login",
                json=login_body,
                headers={
                    "Origin": "https://foreign.example.test",
                    "X-CSRF-Token": admin.cookies["csa_csrf"],
                },
            ).status_code
            == 403
        )
        login = mutate(admin, "/console/v1/auth/login", login_body)
        assert login.status_code == 200
        assert login.json()["role"] == "platform_admin"
        assert login.json()["user"]["must_change_password"] is True
        session_cookie = next(c for c in admin.cookies.jar if c.name == "csa_session")
        assert session_cookie.secure and session_cookie.has_nonstandard_attr("HttpOnly")
        assert session_cookie.get_nonstandard_attr("SameSite") == "lax"
        assert admin.get("/console/v1/admin/users").status_code == 403
        original_session = admin.cookies["csa_session"]
        assert (
            mutate(
                admin,
                "/console/v1/auth/password/change",
                {
                    "current_password": PASSWORD,
                    "new_password": CHANGED_PASSWORD,
                },
            ).status_code
            == 200
        )
        assert admin.cookies["csa_session"] != original_session
        assert admin.get("/console/v1/auth/me").json()["user"]["must_change_password"] is False
        stale = browser()
        stale.cookies.set("csa_session", original_session, domain="127.0.0.1", path="/")
        assert stale.get("/console/v1/auth/me").status_code == 401
        tenants = []
        for _ in range(2):
            created = mutate(
                admin,
                "/console/v1/admin/clients",
                {
                    "slug": f"packaged-{uuid4().hex}",
                    "name": "Synthetic Packaged Tenant",
                },
            )
            assert created.status_code == 201
            tenants.append(created.json()["id"])
        # Business routes need an active orders package, which requires customer_support.
        for key in ("customer_support", "orders_products"):
            activated = mutate(
                admin,
                f"/console/v1/services/{key}/activate?client_id={tenants[0]}",
                {"reference": "synthetic-packaged"},
            )
            assert activated.status_code == 200, activated.text
        order_ids = [f"packaged-{uuid4()}" for _ in tenants]
        async with await psycopg.AsyncConnection.connect(migrated_dsn, autocommit=True) as owner:
            for tenant, order_id in zip(tenants, order_ids, strict=True):
                await owner.execute(
                    "INSERT INTO orders(id,client_id,status,total) VALUES (%s,%s,'shipped',12.34)",
                    (order_id, tenant),
                )
        assert admin.get("/console/v1/business/orders").status_code == 400
        assert (
            admin.get("/console/v1/business/orders", params={"client_id": tenants[0]}).status_code
            == 200
        )
        for role in ("tenant_admin", "tenant_staff", "read_only"):
            invitation = mutate(
                admin,
                "/console/v1/admin/invites",
                {
                    "client_id": tenants[0],
                    "email": f"{role}@example.test",
                    "display_name": "Synthetic Packaged Member",
                    "role": role,
                },
            )
            assert invitation.status_code == 201
            member = browser()
            accepted = mutate(
                member,
                "/console/v1/invites/accept",
                {
                    "token": invitation.json()["token"],
                    "password": PASSWORD,
                },
            )
            assert accepted.status_code == 200 and accepted.json()["role"] == role
            assert member.get("/console/v1/admin/users").status_code == 403
            assert (
                member.get("/console/v1/profile", params={"client_id": tenants[1]}).status_code
                == 403
            )
            assert (
                member.get(
                    "/console/v1/profile",
                    headers={
                        "X-Actor-Role": "platform_admin",
                    },
                ).status_code
                == 403
            )
            orders = member.get("/console/v1/business/orders")
            assert orders.status_code == (403 if role == "read_only" else 200)
            if role != "read_only":
                assert orders.json()["marketplace_sync"] == "unavailable"
                assert [item["id"] for item in orders.json()["items"]] == [order_ids[0]]
                assert member.get(f"/console/v1/business/orders/{order_ids[1]}").status_code == 404
                assert (
                    member.get(
                        "/console/v1/business/orders", params={"client_id": tenants[1]}
                    ).status_code
                    == 403
                )
                page = member.get("/portal/business")
                assert page.status_code == 200
                assert "Local records only, not a live TikTok Shop feed." in page.text
                assert "Stock is not synchronized or authoritative across stores." in page.text
                assert "Not available in this slice" in page.text
            for client in (anonymous, member):
                for path in (
                    "/api/v1/channels/tiktok_shop/oauth/authorize",
                    "/api/v1/channels/tiktok_shop/oauth/callback",
                    "/api/v1/channels/tiktok_shop/webhook",
                    "/api/v1/channels/shopee/webhook",
                ):
                    assert client.get(path).status_code == 404
            csrf = member.cookies["csa_csrf"]
            assert (
                member.post(
                    "/console/v1/auth/logout",
                    headers={
                        "Origin": w.origin,
                        "X-CSRF-Token": "synthetic-wrong-csrf",
                    },
                ).status_code
                == 403
            )
            assert mutate(member, "/console/v1/auth/logout", {}).status_code == 204
            assert member.get("/console/v1/auth/me").status_code == 401
            assert csrf not in member.cookies.values()

        invalid = anonymous.post(
            "/console/v1/auth/login",
            json={
                "email": "invalid",
                "password": PASSWORD,
            },
        )
        assert invalid.status_code == 422
        assert PASSWORD not in invalid.text
        assert all("input" not in error for error in invalid.json()["detail"])

    async with await psycopg.AsyncConnection.connect(migrated_dsn, autocommit=True) as owner:
        await owner.execute("DELETE FROM schema_migrations WHERE version = '0370'")
        try:
            with httpx.Client(trust_env=False) as direct:
                assert direct.get(w.gateway + "/healthz").status_code == 200
                refusal = direct.get(w.gateway + "/readyz")
            assert refusal.status_code == 503 and "0370" in refusal.json()["reason"]
        finally:
            await owner.execute("INSERT INTO schema_migrations(version) VALUES ('0370')")
        identity_faults: tuple[tuple[LiteralString, LiteralString], ...] = (
            (
                "GRANT SELECT ON contacts TO csa_identity",
                "REVOKE SELECT ON contacts FROM csa_identity",
            ),
            (
                "REVOKE SELECT ON console_sessions FROM csa_identity",
                "GRANT SELECT ON console_sessions TO csa_identity",
            ),
        )
        for fault, restore in identity_faults:
            await owner.execute(fault)
            try:
                with httpx.Client(trust_env=False) as direct:
                    refusal = direct.get(w.gateway + "/readyz")
                assert refusal.status_code == 503
                assert refusal.json()["reason"] == "identity database unavailable: RuntimeError"
                for secret in (IDENTITY_PASSWORD, CSA_APP_PASSWORD, PASSWORD):
                    assert secret not in refusal.text
            finally:
                await owner.execute(restore)
        with httpx.Client(trust_env=False) as direct:
            assert direct.get(w.gateway + "/readyz").status_code == 200


async def test_packaged_staging_bootstrap_and_storage(
    packaged_images: tuple[str, str],
    combined_pg_image: str,
) -> None:
    from apps.gateway.main import shipped_versions
    from testcontainers.postgres import PostgresContainer

    with (
        PostgresContainer(
            image=combined_pg_image,
            username="csa",
            password="synthetic_bootstrap_owner_pw",
            dbname="csa_staging",
            driver=None,
        ) as postgres,
        closing(docker.from_env()) as client,
        Network(docker_network_kw={"internal": True}) as network,
        ExitStack() as cleanup,
    ):
        database = postgres.get_wrapped_container()
        assert database.id is not None
        network.connect(database.id, ["staging-db"])
        cleanup.callback(client.networks.get(network.name).disconnect, database.id, force=True)
        volume = client.volumes.create(name=f"csa-bootstrap-storage-{uuid4().hex}")
        cleanup.callback(volume.remove)
        candidate = client.containers.run(
            packaged_images[0],
            ["python", "-c", "import time; time.sleep(600)"],
            detach=True,
            network=network.name,
            read_only=True,
            tmpfs={"/tmp": "", "/run/secrets": "uid=1000,gid=1000,mode=0700"},
            volumes={volume.name: {"bind": "/var/lib/csa/uploads", "mode": "rw"}},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            environment={"NO_LOCAL_ENV": "1", "ENV": "ci"},
        )
        cleanup.callback(candidate.remove, force=True, v=True)
        candidate.reload()
        assert not candidate.attrs["HostConfig"]["PortBindings"]
        uid = candidate.exec_run(["python", "-c", "import os; print(os.getuid(), os.getgid())"])
        assert isinstance(uid.output, bytes)
        assert uid.exit_code == 0 and uid.output.strip() == b"1000 1000"
        owner = "postgresql://csa:synthetic_bootstrap_owner_pw@staging-db:5432/csa_staging"
        secrets = {
            "MIGRATE_DATABASE_URL": owner,
            "LG_SETUP_DATABASE_URL": owner,
            "CSA_APP_PASSWORD": "synthetic_bootstrap_app_pw",
            "CSA_RO_PASSWORD": "synthetic_bootstrap_ro_pw",
            "CSA_IDENTITY_PASSWORD": IDENTITY_PASSWORD,
        }

        def write_secrets(values: dict[str, str]) -> None:
            result = candidate.exec_run(
                [
                    "python",
                    "-c",
                    "import json,os; from pathlib import Path; "
                    "[(Path('/run/secrets')/name).write_text(value) for name,value in "
                    "json.loads(os.environ['SYNTHETIC_SECRET_FILES']).items()]",
                ],
                environment={"SYNTHETIC_SECRET_FILES": json.dumps(values)},
            )
            assert result.exit_code == 0

        def migrate(approval: str) -> tuple[int, bytes]:
            result = candidate.exec_run(
                ["python", "-m", "scripts.staging_migrate"],
                environment={"CSA_MIGRATION_APPROVAL": approval},
            )
            assert isinstance(result.exit_code, int) and isinstance(result.output, bytes)
            return result.exit_code, result.output

        host_dsn = (
            "postgresql://csa:synthetic_bootstrap_owner_pw@"
            f"{postgres.get_container_host_ip()}:{postgres.get_exposed_port(5432)}/csa_staging"
        )

        async def snapshot() -> list[tuple[object, ...]]:
            async with await psycopg.AsyncConnection.connect(host_dsn) as conn:
                return await (
                    await conn.execute(
                        "SELECT nspname,relname,relkind::text FROM pg_class "
                        "JOIN pg_namespace ON pg_namespace.oid=relnamespace "
                        "WHERE nspname IN ('public','lg') ORDER BY 1,2,3"
                    )
                ).fetchall()

        empty = await snapshot()
        code, output = migrate("disabled")
        assert code != 0 and b"approval required" in output
        assert b"FileNotFoundError" not in output
        assert await snapshot() == empty

        baseline = candidate.exec_run(["cat", "/app/db/schema_baseline.sql"])
        assert baseline.exit_code == 0
        assert isinstance(baseline.output, bytes)
        postgres.copy_into_container(baseline.output, "/tmp/packaged-baseline.sql")
        code, output = postgres.exec(
            [
                "psql",
                "-U",
                "csa",
                "-d",
                "csa_staging",
                "-v",
                "ON_ERROR_STOP=1",
                "-f",
                "/tmp/packaged-baseline.sql",
            ]
        )
        assert isinstance(output, bytes)
        assert code == 0, output.decode()
        before = await snapshot()
        for target in ("MIGRATE_DATABASE_URL", "LG_SETUP_DATABASE_URL"):
            write_secrets({**secrets, target: owner.replace("/csa_staging", "/wrong_database")})
            code, output = migrate("approved-isolated-staging")
            assert code != 0 and b"target must be the isolated staging database" in output
            assert owner.encode() not in output
            assert await snapshot() == before
            async with await psycopg.AsyncConnection.connect(host_dsn) as conn:
                assert (
                    await (
                        await conn.execute(
                            "SELECT rolname FROM pg_roles WHERE rolname IN "
                            "('csa_app','csa_ro','csa_identity')"
                        )
                    ).fetchall()
                    == []
                )
        write_secrets(secrets)
        code, output = migrate("approved-isolated-staging")
        assert code == 0, output.decode()
        async with await psycopg.AsyncConnection.connect(host_dsn) as conn:
            ledger = await (
                await conn.execute(
                    "SELECT version,applied_at FROM schema_migrations ORDER BY version"
                )
            ).fetchall()
            assert {row[0] for row in ledger} == set(shipped_versions())
            assert (
                await (
                    await conn.execute(
                        "SELECT to_regclass('lg.checkpoints'),to_regclass('lg.checkpoint_blobs'),"
                        "to_regclass('lg.checkpoint_writes')"
                    )
                ).fetchone()
            ) == ("lg.checkpoints", "lg.checkpoint_blobs", "lg.checkpoint_writes")
        code, output = migrate("approved-isolated-staging")
        assert code == 0, output.decode()
        async with await psycopg.AsyncConnection.connect(host_dsn) as conn:
            assert (
                await (
                    await conn.execute(
                        "SELECT version,applied_at FROM schema_migrations ORDER BY version"
                    )
                ).fetchall()
                == ledger
            )

        bootstrap = [
            "python",
            "-c",
            "import asyncio,os; from scripts.bootstrap_console_admin import run; "
            "asyncio.run(run('bootstrap@example.test','Synthetic Bootstrap',"
            "os.environ['SYNTHETIC_INITIAL_PASSWORD']))",
        ]
        identity = owner.replace(
            "csa:synthetic_bootstrap_owner_pw", f"csa_identity:{IDENTITY_PASSWORD}"
        )
        environment = {
            "IDENTITY_DATABASE_URL": identity,
            "SYNTHETIC_INITIAL_PASSWORD": PASSWORD,
        }
        first = candidate.exec_run(bootstrap, environment=environment)
        assert isinstance(first.output, bytes)
        assert first.exit_code == 0 and b"password change required" in first.output
        second = candidate.exec_run(bootstrap, environment=environment)
        assert isinstance(second.output, bytes)
        assert second.exit_code != 0 and b"bootstrap is closed" in second.output
        assert PASSWORD.encode() not in first.output + second.output
        async with await psycopg.AsyncConnection.connect(host_dsn) as conn:
            row = await (
                await conn.execute(
                    "SELECT email,must_change_password,is_platform_admin FROM console_users"
                )
            ).fetchall()
            assert row == [("bootstrap@example.test", True, True)]
            assert (
                await (
                    await conn.execute(
                        "SELECT count(*) FROM console_audit WHERE action='identity.bootstrap'"
                    )
                ).fetchone()
            ) == (1,)

        write_probe = [
            "python",
            "-c",
            "from pathlib import Path; p=Path('/var/lib/csa/uploads/synthetic-probe'); "
            "p.write_text('synthetic storage'); assert p.read_text()=='synthetic storage'; "
            "assert p.stat().st_uid==1000 and p.stat().st_gid==1000; p.unlink()",
        ]
        denied = candidate.exec_run(write_probe)
        assert denied.exit_code != 0 and b"PermissionError" in denied.output
        ownership = candidate.exec_run(
            ["chown", "1000:1000", "/var/lib/csa/uploads"],
            user="0",
        )
        # CHOWN is deliberately absent from the application container's capabilities.
        assert ownership.exit_code != 0
        initializer = client.containers.create(
            packaged_images[0],
            ["chown", "1000:1000", "/var/lib/csa/uploads"],
            user="0",
            network_mode="none",
            read_only=True,
            cap_drop=["ALL"],
            cap_add=["CHOWN"],
            volumes={volume.name: {"bind": "/var/lib/csa/uploads", "mode": "rw"}},
        )
        cleanup.callback(initializer.remove, force=True, v=True)
        initializer.start()
        assert initializer.wait(timeout=30)["StatusCode"] == 0
        assert candidate.exec_run(write_probe).exit_code == 0


async def test_packaged_synthetic_backup_restore(
    packaged_images: tuple[str, str],
    combined_pg_image: str,
    pg_container: object,
    lg_setup_dsn: str,
) -> None:
    from testcontainers.postgres import PostgresContainer

    tenants = [uuid4(), uuid4()]
    user_id = uuid4()
    email = f"restore-{user_id}@example.test"
    async with await psycopg.AsyncConnection.connect(lg_setup_dsn, autocommit=True) as conn:
        for tenant in tenants:
            await conn.execute(
                "INSERT INTO clients(id,slug,name,platform_version) VALUES (%s,%s,%s,'test')",
                (tenant, f"restore-{tenant}", "Synthetic Restore Tenant"),
            )
        await conn.execute(
            "INSERT INTO console_users(id,email,display_name) VALUES (%s,%s,'Synthetic Restore')",
            (user_id, email),
        )
        await conn.execute(
            "INSERT INTO console_memberships(user_id,client_id,role) VALUES (%s,%s,'read_only')",
            (user_id, tenants[0]),
        )
        await conn.execute(
            "INSERT INTO console_audit(actor_id,client_id,action,target_id) "
            "VALUES (%s,%s,'synthetic.restore',%s)",
            (user_id, tenants[0], str(user_id)),
        )
        ledger = await (
            await conn.execute("SELECT version,applied_at FROM schema_migrations ORDER BY version")
        ).fetchall()

    code, roles = pg_container.exec(  # type: ignore[attr-defined]
        ["pg_dumpall", "-U", "csa", "--roles-only", "--no-role-passwords"]
    )
    assert code == 0 and isinstance(roles, bytes)
    assert b"PASSWORD" not in roles
    # Testcontainers already creates the owner; retain its attributes, not CREATE.
    assert roles.splitlines().count(b"CREATE ROLE csa;") == 1
    roles = b"\n".join(line for line in roles.splitlines() if line != b"CREATE ROLE csa;")
    code, dump = pg_container.exec(  # type: ignore[attr-defined]
        ["pg_dump", "-U", "csa", "-d", "csa", "--format=custom"]
    )
    assert code == 0 and isinstance(dump, bytes) and dump.startswith(b"PGDMP")

    with (
        PostgresContainer(
            image=combined_pg_image,
            username="csa",
            password="synthetic_restore_owner_pw",
            dbname="csa_restore",
            driver=None,
        ) as target,
        closing(docker.from_env()) as client,
        Network(docker_network_kw={"internal": True}) as network,
        ExitStack() as cleanup,
    ):
        database = target.get_wrapped_container()
        assert database.id is not None
        network.connect(database.id, ["restore-db"])
        cleanup.callback(client.networks.get(network.name).disconnect, database.id, force=True)
        target.copy_into_container(roles, "/tmp/synthetic-roles.sql")
        target.copy_into_container(dump, "/tmp/synthetic-backup.dump")
        code, output = target.exec(
            [
                "psql",
                "-U",
                "csa",
                "-d",
                "csa_restore",
                "-v",
                "ON_ERROR_STOP=1",
                "-f",
                "/tmp/synthetic-roles.sql",
            ]
        )
        assert isinstance(output, bytes)
        assert code == 0, output.decode()
        code, output = target.exec(
            [
                "pg_restore",
                "-U",
                "csa",
                "-d",
                "csa_restore",
                "--exit-on-error",
                "--single-transaction",
                "/tmp/synthetic-backup.dump",
            ]
        )
        assert isinstance(output, bytes)
        assert code == 0, output.decode()
        host_dsn = (
            "postgresql://csa:synthetic_restore_owner_pw@"
            f"{target.get_container_host_ip()}:{target.get_exposed_port(5432)}/csa_restore"
        )
        async with await psycopg.AsyncConnection.connect(host_dsn, autocommit=True) as conn:
            assert (
                await (
                    await conn.execute(
                        "SELECT version,applied_at FROM schema_migrations ORDER BY version"
                    )
                ).fetchall()
                == ledger
            )
            assert await (
                await conn.execute(
                    "SELECT id FROM clients WHERE id=ANY(%s) ORDER BY id",
                    (tenants,),
                )
            ).fetchall() == [(tenant,) for tenant in sorted(tenants)]
            assert await (
                await conn.execute(
                    "SELECT email FROM console_users WHERE id=%s",
                    (user_id,),
                )
            ).fetchall() == [(email,)]
            assert await (
                await conn.execute(
                    "SELECT client_id,role FROM console_memberships WHERE user_id=%s",
                    (user_id,),
                )
            ).fetchall() == [(tenants[0], "read_only")]
            assert await (
                await conn.execute(
                    "SELECT actor_id,client_id,target_id FROM console_audit "
                    "WHERE action='synthetic.restore' AND actor_id=%s",
                    (user_id,),
                )
            ).fetchall() == [(user_id, tenants[0], str(user_id))]
            assert await (
                await conn.execute(
                    "SELECT to_regclass('lg.checkpoints'),to_regclass('lg.checkpoint_blobs'),"
                    "to_regclass('lg.checkpoint_writes')"
                )
            ).fetchone() == ("lg.checkpoints", "lg.checkpoint_blobs", "lg.checkpoint_writes")
            # Role hashes are intentionally not in the backup; provision new credentials.
            for role, password in (
                ("csa_app", "synthetic_restored_app_pw"),
                ("csa_identity", "synthetic_restored_identity_pw"),
            ):
                await conn.execute(
                    sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role),
                        sql.Literal(password),
                    )
                )

        probe = """
import asyncio, os
import psycopg
from apps.gateway.main import check_readiness
from csa.console.identity import IdentityConfig, IdentityService
from csa.core.db import build_pool
from csa.core.tenancy import tx

async def run():
    domain = build_pool(os.environ['DATABASE_URL'], min_size=1, max_size=1, autocommit=True)
    identity = build_pool(os.environ['IDENTITY_DATABASE_URL'], min_size=1, max_size=1,
                          autocommit=True)
    await domain.open(wait=True)
    await identity.open(wait=True)
    try:
        ready, reason = await check_readiness(domain)
        if os.environ['EXPECT_READY'] == 'false':
            assert not ready and '0370' in reason
            print('incomplete restore refused')
            return
        assert ready, reason
        await IdentityService(identity, IdentityConfig(origins=('https://restore.example.test',))).check_ready()
        for tenant, expected in ((os.environ['TENANT'], 1), (os.environ['FOREIGN_TENANT'], 0)):
            async with tx(identity, tenant) as conn:
                rows = await (await conn.execute(
                    'SELECT client_id FROM console_memberships WHERE user_id=%s',
                    (os.environ['USER_ID'],))).fetchall()
                assert len(rows) == expected
        for pool, statement in (
            (domain, 'SELECT * FROM console_sessions'),
            (identity, 'SELECT * FROM clients'),
            (identity, "UPDATE console_audit SET action='tampered'"),
        ):
            async with pool.connection() as conn:
                try:
                    await conn.execute(statement)
                except psycopg.errors.InsufficientPrivilege:
                    pass
                else:
                    raise AssertionError('restored privilege boundary missing')
        print('restored readiness and role boundaries passed')
    finally:
        await identity.close()
        await domain.close()

asyncio.run(run())
"""

        def packaged_probe(expected: bool) -> bytes:
            container = client.containers.create(
                packaged_images[0],
                ["python", "-c", probe],
                network=network.name,
                read_only=True,
                tmpfs={"/tmp": ""},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                environment={
                    "NO_LOCAL_ENV": "1",
                    "ENV": "ci",
                    "DATABASE_URL": "postgresql://csa_app:synthetic_restored_app_pw@restore-db:5432/csa_restore",
                    "IDENTITY_DATABASE_URL": "postgresql://csa_identity:synthetic_restored_identity_pw@restore-db:5432/csa_restore",
                    "EXPECT_READY": str(expected).lower(),
                    "TENANT": str(tenants[0]),
                    "FOREIGN_TENANT": str(tenants[1]),
                    "USER_ID": str(user_id),
                },
            )
            try:
                container.start()
                status = container.wait(timeout=60)
                logs = container.logs()
                assert status["StatusCode"] == 0, logs.decode()
                return logs
            finally:
                container.remove(force=True, v=True)

        assert b"restored readiness and role boundaries passed" in packaged_probe(True)
        async with await psycopg.AsyncConnection.connect(host_dsn, autocommit=True) as conn:
            await conn.execute("DELETE FROM schema_migrations WHERE version='0370'")
            assert b"incomplete restore refused" in packaged_probe(False)
            await conn.execute(
                "INSERT INTO schema_migrations(version,applied_at) VALUES (%s,%s)",
                next(row for row in ledger if row[0] == "0370"),
            )
        assert b"restored readiness and role boundaries passed" in packaged_probe(True)


async def test_packaged_m2_browser_acceptance(
    packaged_console: PackagedConsole,
    migrated_dsn: str,
) -> None:
    w = packaged_console
    tenant_admin_email = f"browser-admin-{uuid4().hex}@example.test"
    reader_email = f"browser-reader-{uuid4().hex}@example.test"
    member_password = "synthetic-browser-member-password"
    tenants: list[str] = []

    def api_client() -> httpx.Client:
        client = httpx.Client(
            base_url=w.origin,
            verify=w.tls,
            trust_env=False,
            timeout=30,
        )
        response = client.get("/console/v1/auth/csrf")
        assert response.status_code == 200
        assert response.json()["csrf_token"] == client.cookies["csa_csrf"]
        return client

    def mutate(client: httpx.Client, path: str, body: object) -> httpx.Response:
        return client.post(
            path,
            json=body,
            headers={
                "Origin": w.origin,
                "X-CSRF-Token": client.cookies["csa_csrf"],
            },
        )

    page_errors: list[bool] = []
    egress_attempts: list[str] = []
    console_requests: list[tuple[str, str, str]] = []
    fail_run_list = False
    browser_contexts: list[BrowserContext] = []

    async def login(page: Page, email: str, password: str, destination: str) -> None:
        await page.goto(f"{w.origin}/login")
        await page.get_by_label("Email", exact=True).fill(email)
        await page.get_by_label("Password", exact=True).fill(password)
        await page.get_by_role("button", name="Sign in", exact=True).click()
        await page.wait_for_url(f"{w.origin}{destination}")

    async def invoice_card(page: Page) -> Locator:
        # Agents live in collapsed package cards on the Services tab; invoices is orders_products.
        await page.get_by_test_id("services-agents-toggle-orders_products").click()
        agents = page.get_by_test_id("services-agents-orders_products")
        return agents.get_by_role("heading", name="Invoices").locator("xpath=..")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()

        async def route_request(route: Route) -> None:
            nonlocal fail_run_list
            target = urlsplit(route.request.url)
            request_origin = f"{target.scheme}://{target.netloc}"
            if request_origin != w.origin:
                egress_attempts.append(request_origin)
                await route.abort()
                return
            if (
                fail_run_list
                and target.path == "/console/v1/runs"
                and route.request.method == "GET"
            ):
                await route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"detail":"synthetic transient failure"}',
                )
                return
            await route.continue_()

        async def record_request(request: Request) -> None:
            target = urlsplit(request.url)
            if target.path.startswith("/console/v1/"):
                console_requests.append(
                    (request.method, f"{target.scheme}://{target.netloc}", target.path)
                )

        async def new_page() -> tuple[BrowserContext, Page]:
            context = await browser.new_context(
                ignore_https_errors=True,
                service_workers="block",
            )
            browser_contexts.append(context)
            await context.route("**/*", route_request)
            page = await context.new_page()
            page.on("pageerror", lambda _: page_errors.append(True))
            page.on("request", record_request)
            return context, page

        try:
            admin_context, admin_page = await new_page()
            await login(
                admin_page,
                "packaged-admin@example.test",
                PASSWORD,
                "/change-password",
            )
            await admin_page.get_by_label("Current password", exact=True).fill(PASSWORD)
            await admin_page.get_by_label("New password", exact=True).fill(CHANGED_PASSWORD)
            await admin_page.get_by_label("Confirm new password", exact=True).fill(CHANGED_PASSWORD)
            await admin_page.get_by_role("button", name="Save password", exact=True).click()
            await admin_page.wait_for_url(f"{w.origin}/")

            setup_admin = api_client()
            try:
                login_response = mutate(
                    setup_admin,
                    "/console/v1/auth/login",
                    {
                        "email": "packaged-admin@example.test",
                        "password": CHANGED_PASSWORD,
                    },
                )
                assert login_response.status_code == 200
                assert login_response.json()["role"] == "platform_admin"
                for suffix in ("alpha", "beta"):
                    created = mutate(
                        setup_admin,
                        "/console/v1/admin/clients",
                        {
                            "slug": f"browser-{suffix}-{uuid4().hex[:12]}",
                            "name": f"Synthetic Browser Tenant {suffix.title()}",
                        },
                    )
                    assert created.status_code == 201, created.text
                    tenants.append(created.json()["id"])
                for tenant, email, role in (
                    (tenants[0], tenant_admin_email, "tenant_admin"),
                    (tenants[1], reader_email, "read_only"),
                ):
                    invitation = mutate(
                        setup_admin,
                        "/console/v1/admin/invites",
                        {
                            "client_id": tenant,
                            "email": email,
                            "display_name": "Synthetic Browser Member",
                            "role": role,
                        },
                    )
                    assert invitation.status_code == 201, invitation.text
                    member = api_client()
                    try:
                        accepted = mutate(
                            member,
                            "/console/v1/invites/accept",
                            {
                                "token": invitation.json()["token"],
                                "password": member_password,
                            },
                        )
                    finally:
                        member.close()
                    assert accepted.status_code == 200, accepted.text
                    assert accepted.json()["role"] == role
            finally:
                setup_admin.close()

            run_ids: dict[tuple[str, str], int] = {}
            traces: dict[tuple[str, str], str] = {}
            async with await psycopg.AsyncConnection.connect(
                migrated_dsn, autocommit=True
            ) as owner:
                for tenant, agent, suffix, ok in (
                    (tenants[0], "invoice_agent", "alpha-invoice", True),
                    (tenants[0], "booking_agent", "alpha-booking", False),
                    (tenants[1], "invoice_agent", "beta-invoice", True),
                ):
                    trace_id = uuid4()
                    cursor = await owner.execute(
                        "INSERT INTO agent_runs(client_id,trace_id,agent,ok,status,created_at) "
                        "VALUES (%s,%s,%s,%s,%s,now()) RETURNING id",
                        (tenant, trace_id, agent, ok, "ok" if ok else "error"),
                    )
                    run_row = await cursor.fetchone()
                    assert run_row is not None
                    run_id = run_row[0]
                    await owner.execute(
                        "INSERT INTO agent_events(client_id,trace_id,node,agent,status,"
                        "duration_ms,summary) VALUES (%s,%s,%s,%s,%s,%s,'{}'::jsonb)",
                        (tenant, trace_id, f"browser_seed_{suffix}", agent, "ok", 17),
                    )
                    run_ids[(tenant, agent)] = run_id
                    traces[(tenant, agent)] = str(trace_id)

            await admin_page.goto(f"{w.origin}/services?client_id={tenants[0]}")
            await admin_page.get_by_test_id("services-tenant").select_option(tenants[0])
            admin_invoice = await invoice_card(admin_page)
            await admin_invoice.get_by_role("button", name="Subscribe", exact=True).click()
            await admin_invoice.get_by_text("Subscribed", exact=True).wait_for()
            await admin_invoice.get_by_text("Off", exact=True).wait_for()

            await admin_page.reload()
            await admin_page.get_by_test_id("services-tenant").select_option(tenants[0])
            admin_invoice = await invoice_card(admin_page)
            await admin_invoice.get_by_text("Subscribed", exact=True).wait_for()
            await admin_page.get_by_test_id("services-tenant").select_option(tenants[1])
            other_invoice = await invoice_card(admin_page)
            await other_invoice.get_by_text("Not subscribed", exact=True).wait_for()

            _, tenant_page = await new_page()
            await login(tenant_page, tenant_admin_email, member_password, "/portal")
            await tenant_page.goto(f"{w.origin}/portal/services")
            tenant_invoice = await invoice_card(tenant_page)
            assert (
                await tenant_page.get_by_role("button", name="Subscribe", exact=True).count() == 0
            )
            await tenant_invoice.get_by_role(
                "button", name="Turn tenant switch on", exact=True
            ).click()
            await tenant_invoice.get_by_text("On", exact=True).wait_for()
            await tenant_page.reload()
            tenant_invoice = await invoice_card(tenant_page)
            await tenant_invoice.get_by_text("On", exact=True).wait_for()
            forbidden_tenant = await tenant_page.evaluate(
                """async (clientId) => (await fetch(
                  `/console/v1/agents?client_id=${encodeURIComponent(clientId)}`
                )).status""",
                tenants[1],
            )
            assert forbidden_tenant == 403

            async with await psycopg.AsyncConnection.connect(
                migrated_dsn, autocommit=True
            ) as owner:
                state = await owner.execute(
                    "SELECT is_enabled,client_enabled FROM capability_registry "
                    "WHERE client_id=%s AND name='invoice_agent'",
                    (tenants[0],),
                )
                assert await state.fetchone() == (True, True)
                other_state = await owner.execute(
                    "SELECT is_enabled,client_enabled FROM capability_registry "
                    "WHERE client_id=%s AND name='invoice_agent'",
                    (tenants[1],),
                )
                assert await other_state.fetchone() == (False, False)
                audit = await owner.execute(
                    "SELECT actor_id,details FROM console_audit "
                    "WHERE client_id=%s AND action='capability.updated' "
                    "AND target_id='invoice_agent' ORDER BY id",
                    (tenants[0],),
                )
                audit_rows = await audit.fetchall()
                admin_user = await owner.execute(
                    "SELECT id FROM console_users WHERE email='packaged-admin@example.test'"
                )
                admin_row = await admin_user.fetchone()
                assert admin_row is not None
                admin_id = admin_row[0]
                tenant_user = await owner.execute(
                    "SELECT id FROM console_users WHERE email=%s",
                    (tenant_admin_email,),
                )
                tenant_row = await tenant_user.fetchone()
                assert tenant_row is not None
                tenant_admin_id = tenant_row[0]
                assert len(audit_rows) == 2
                audit_by_actor = {actor: details["after"] for actor, details in audit_rows}
                assert audit_by_actor == {
                    admin_id: {"is_enabled": True, "client_enabled": False},
                    tenant_admin_id: {"is_enabled": True, "client_enabled": True},
                }

            await admin_page.goto(f"{w.origin}/activity")
            await admin_page.get_by_label("Tenant").select_option(tenants[0])
            await admin_page.get_by_label("Agent name (optional)").fill("invoice_agent")
            await admin_page.get_by_role("button", name="Filter / refresh", exact=True).click()
            invoice_run = run_ids[(tenants[0], "invoice_agent")]
            invoice_row = admin_page.get_by_role(
                "button", name=re.compile(rf"#{invoice_run}\s+invoice_agent")
            )
            await invoice_row.wait_for()
            booking_run = run_ids[(tenants[0], "booking_agent")]
            assert (
                await admin_page.get_by_role(
                    "button", name=re.compile(rf"#{booking_run}\s+booking_agent")
                ).count()
                == 0
            )
            await invoice_row.click()
            await admin_page.get_by_role(
                "heading", name=f"Run #{invoice_run}", exact=True
            ).wait_for()
            await admin_page.get_by_text(
                "browser_seed_alpha-invoice / invoice_agent / ok / 17 ms"
            ).wait_for()
            async with await psycopg.AsyncConnection.connect(
                migrated_dsn, autocommit=True
            ) as owner:
                await owner.execute(
                    "INSERT INTO agent_events(client_id,trace_id,node,agent,status,"
                    "duration_ms,summary) VALUES (%s,%s,%s,%s,'ok',23,'{}'::jsonb)",
                    (
                        tenants[0],
                        traces[(tenants[0], "invoice_agent")],
                        "browser_poll_followup",
                        "invoice_agent",
                    ),
                )
            await admin_page.get_by_text(
                "browser_poll_followup / invoice_agent / ok / 23 ms"
            ).wait_for(timeout=12_000)

            fail_run_list = True
            await admin_page.get_by_role("button", name="Filter / refresh", exact=True).click()
            await (
                admin_page.get_by_role("alert")
                .filter(has_text="synthetic transient failure")
                .wait_for()
            )
            await admin_page.get_by_text("Runtime data unavailable.", exact=True).wait_for()
            fail_run_list = False
            await admin_page.get_by_role("button", name="Filter / refresh", exact=True).click()
            await invoice_row.wait_for()
            await (
                admin_page.get_by_role("alert")
                .filter(has_text="synthetic transient failure")
                .wait_for(state="detached")
            )

            _, reader_page = await new_page()
            await login(reader_page, reader_email, member_password, "/portal")
            await reader_page.goto(f"{w.origin}/portal/services")
            reader_invoice = await invoice_card(reader_page)
            await reader_invoice.get_by_text("Not subscribed", exact=True).wait_for()
            assert (
                await reader_page.get_by_role(
                    "button", name=re.compile(r"Subscribe|Turn tenant switch")
                ).count()
                == 0
            )
            forbidden_runs = await reader_page.evaluate(
                """async (clientId) => (await fetch(
                  `/console/v1/runs?client_id=${encodeURIComponent(clientId)}`
                )).status""",
                tenants[0],
            )
            assert forbidden_runs == 403
            await reader_page.goto(f"{w.origin}/portal/activity")
            beta_run = run_ids[(tenants[1], "invoice_agent")]
            beta_row = reader_page.get_by_role(
                "button", name=re.compile(rf"#{beta_run}\s+invoice_agent")
            )
            await beta_row.wait_for()
            assert (
                await reader_page.get_by_role(
                    "button", name=re.compile(rf"#{invoice_run}\s+invoice_agent")
                ).count()
                == 0
            )
            await tenant_page.goto(f"{w.origin}/portal/activity")
            tenant_invoice_run = run_ids[(tenants[0], "invoice_agent")]
            await tenant_page.get_by_role(
                "button", name=re.compile(rf"#{tenant_invoice_run}\s+invoice_agent")
            ).wait_for()
            assert (
                await tenant_page.get_by_role(
                    "button", name=re.compile(rf"#{beta_run}\s+invoice_agent")
                ).count()
                == 0
            )

            session_cookie = next(
                cookie
                for cookie in await admin_context.cookies(w.origin)
                if cookie.get("name") == "csa_session"
            )
            stale_session = session_cookie.get("value")
            assert stale_session is not None
            await admin_page.goto(f"{w.origin}/services")
            await admin_page.get_by_role("button", name="Sign out", exact=True).click()
            await admin_page.wait_for_url(f"{w.origin}/login")
            await admin_page.go_back()
            await admin_page.reload()
            await admin_page.get_by_role("heading", name="Sign in", exact=True).wait_for()
            assert urlsplit(admin_page.url).path == "/login"
            with httpx.Client(
                base_url=w.origin,
                verify=w.tls,
                trust_env=False,
                timeout=30,
                cookies={"csa_session": stale_session},
            ) as stale:
                assert stale.get("/console/v1/auth/me").status_code == 401
            for _, request_origin, _ in console_requests:
                assert request_origin == w.origin
            assert any(
                method == "POST" and path == "/console/v1/auth/login"
                for method, _, path in console_requests
            )
            assert any(
                method == "POST" and path == "/console/v1/auth/password/change"
                for method, _, path in console_requests
            )
            assert any(
                method == "POST" and path == "/console/v1/auth/logout"
                for method, _, path in console_requests
            )
            assert any(
                method == "PATCH" and path == "/console/v1/capabilities/invoice_agent"
                for method, _, path in console_requests
            )
            assert not egress_attempts
            assert not page_errors
        finally:
            for context in reversed(browser_contexts):
                await context.close()
            await browser.close()


async def test_packaged_customer_inbox_browser_acceptance(
    packaged_console: PackagedConsole,
    migrated_dsn: str,
) -> None:
    """Exercise staff inbox UX and durable outcomes without a delivery worker/provider."""
    w = packaged_console
    password = "synthetic-inbox-browser-password"
    emails = {
        name: f"inbox-{name}-{uuid4().hex}@example.test"
        for name in ("staff", "admin", "reader", "foreign")
    }
    tenants: list[str] = []
    conversation, contact, credential, endpoint, trace = (uuid4() for _ in range(5))
    contexts: list[BrowserContext] = []
    egress: list[str] = []
    page_errors: list[str] = []
    replies: list[str | None] = []
    lost_ack = False
    reply_text = "Synthetic staff answer awaiting delivery"

    with httpx.Client(base_url=w.origin, verify=w.tls, trust_env=False, timeout=30) as admin:

        def mutate(path: str, body: object) -> httpx.Response:
            csrf = admin.get("/console/v1/auth/csrf").json()["csrf_token"]
            return admin.post(path, json=body, headers={"Origin": w.origin, "X-CSRF-Token": csrf})

        assert (
            mutate(
                "/console/v1/auth/login",
                {"email": "packaged-admin@example.test", "password": PASSWORD},
            ).status_code
            == 200
        )
        assert (
            mutate(
                "/console/v1/auth/password/change",
                {"current_password": PASSWORD, "new_password": CHANGED_PASSWORD},
            ).status_code
            == 200
        )
        for label in ("alpha", "beta"):
            created = mutate(
                "/console/v1/admin/clients",
                {"slug": f"inbox-{label}-{uuid4().hex[:12]}", "name": f"Synthetic Inbox {label}"},
            )
            assert created.status_code == 201, created.text
            tenants.append(created.json()["id"])
        # The inbox is gated by customer_support; both tenants hold it for isolation checks.
        for tenant in tenants:
            activated = mutate(
                f"/console/v1/services/customer_support/activate?client_id={tenant}",
                {"reference": "synthetic-packaged"},
            )
            assert activated.status_code == 200, activated.text
        for name, email in emails.items():
            invited = mutate(
                "/console/v1/admin/invites",
                {
                    "client_id": tenants[1] if name == "foreign" else tenants[0],
                    "email": email,
                    "display_name": f"Synthetic Inbox {name}",
                    "role": "tenant_admin"
                    if name == "admin"
                    else ("read_only" if name == "reader" else "tenant_staff"),
                },
            )
            assert invited.status_code == 201, invited.text
            with httpx.Client(base_url=w.origin, verify=w.tls, trust_env=False) as member:
                csrf = member.get("/console/v1/auth/csrf").json()["csrf_token"]
                accepted = member.post(
                    "/console/v1/invites/accept",
                    json={"token": invited.json()["token"], "password": password},
                    headers={"Origin": w.origin, "X-CSRF-Token": csrf},
                )
                assert accepted.status_code == 200, accepted.text

    async with await psycopg.AsyncConnection.connect(migrated_dsn, autocommit=True) as owner:
        await owner.execute(
            "INSERT INTO contacts(id,client_id) VALUES (%s,%s)", (contact, tenants[0])
        )
        await owner.execute(
            "INSERT INTO conversations(id,client_id,contact_id,channel,channel_thread) "
            "VALUES (%s,%s,%s,'telegram',%s)",
            (conversation, tenants[0], contact, str(contact.int)),
        )
        await owner.execute(
            "INSERT INTO channel_credentials(id,client_id,channel,access_token) "
            "VALUES (%s,%s,'telegram','synthetic-not-a-token')",
            (credential, tenants[0]),
        )
        await owner.execute(
            "INSERT INTO webhook_endpoints(id,client_id,public_id,credential_id) "
            "VALUES (%s,%s,%s,%s)",
            (endpoint, tenants[0], f"synthetic-inbox-{uuid4().hex}", credential),
        )
        await owner.execute(
            "INSERT INTO ingress_events(client_id,endpoint_id,contact_id,idempotency_key,"
            "payload_hash,trace_id,raw,status) "
            "VALUES (%s,%s,%s,%s,'synthetic',%s,'{}','completed')",
            (tenants[0], endpoint, contact, f"synthetic:{trace}", trace),
        )
        await owner.execute(
            "INSERT INTO messages(client_id,conversation_id,direction,author,body,"
            "idempotency_key,trace_id) VALUES (%s,%s,'inbound','customer',%s,%s,%s)",
            (
                tenants[0],
                conversation,
                "Question from synthetic@example.test",
                f"{trace}:in",
                trace,
            ),
        )

    async def fetch(
        page: Page, path: str, body: object | None = None, *, csrf: bool = True
    ) -> dict:
        return await page.evaluate(
            """async ({path, body, csrf}) => {
                const headers = {};
                if (body !== null) {
                    headers['Content-Type'] = 'application/json';
                    if (csrf) {
                        const token = await (await fetch('/console/v1/auth/csrf')).json();
                        headers['X-CSRF-Token'] = token.csrf_token;
                    }
                }
                const response = await fetch(path, {method: body === null ? 'GET' : 'POST',
                    headers, ...(body === null ? {} : {body: JSON.stringify(body)})});
                return {status: response.status, body: await response.json()};
            }""",
            {"path": path, "body": body, "csrf": csrf},
        )

    base = f"/console/v1/inbox/conversations/{conversation}"
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()

        async def route_request(route: Route) -> None:
            nonlocal lost_ack
            target = urlsplit(route.request.url)
            if f"{target.scheme}://{target.netloc}" != w.origin:
                egress.append(f"{target.scheme}://{target.netloc}")
                await route.abort()
                return
            if target.path == f"{base}/reply" and route.request.method == "POST":
                replies.append(route.request.post_data)
                if not lost_ack:
                    committed = await route.fetch()
                    assert committed.status == 202
                    lost_ack = True
                    await route.abort("connectionreset")
                    return
            await route.continue_()

        async def new_page(name: str | None = None) -> Page:
            context = await browser.new_context(ignore_https_errors=True, service_workers="block")
            contexts.append(context)
            await context.route("**/*", route_request)
            page = await context.new_page()
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            if name is not None:
                await page.goto(f"{w.origin}/login")
                await page.get_by_label("Email", exact=True).fill(
                    "packaged-admin@example.test" if name == "platform" else emails[name]
                )
                await page.get_by_label("Password", exact=True).fill(
                    CHANGED_PASSWORD if name == "platform" else password
                )
                await page.get_by_role("button", name="Sign in", exact=True).click()
                await page.wait_for_url(
                    f"{w.origin}/" if name == "platform" else f"{w.origin}/portal"
                )
            return page

        try:
            anonymous = await new_page()
            await anonymous.goto(f"{w.origin}/portal/inbox")
            await anonymous.wait_for_url(f"{w.origin}/login")
            assert (await fetch(anonymous, "/console/v1/inbox/conversations"))["status"] == 401

            staff = await new_page("staff")
            await staff.goto(f"{w.origin}/portal/inbox")
            # The assistant dock button only; its turns are not driven (the planner is off).
            await staff.get_by_test_id("assistant-open").wait_for()
            await staff.get_by_role(
                "button", name=f"{str(conversation)[:8]} · Telegram", exact=False
            ).click()
            await staff.get_by_text("Question from [EMAIL_0]", exact=True).wait_for()
            assert await staff.get_by_text("synthetic@example.test", exact=False).count() == 0
            assert (
                await fetch(
                    staff,
                    f"{base}/takeover",
                    {
                        "request_key": str(uuid4()),
                        "expected_version": 0,
                    },
                    csrf=False,
                )
            )["status"] == 403
            assert (await fetch(staff, f"/console/v1/inbox/conversations?client_id={tenants[1]}"))[
                "status"
            ] == 403
            await staff.get_by_role("button", name="Take over", exact=True).click()
            await staff.get_by_text(
                "Human support owns this conversation. AI paused.", exact=True
            ).wait_for()
            await staff.get_by_label("Human reply", exact=True).fill(reply_text)
            await staff.get_by_role("button", name="Queue reply", exact=True).click()
            await staff.get_by_text("Outcome uncertain.", exact=False).wait_for()
            await staff.get_by_role("list").get_by_text(reply_text, exact=True).wait_for()
            await staff.get_by_role("button", name="Retry the same action", exact=True).click()
            await staff.get_by_text(
                "Reply queued. Delivery status appears in the conversation.", exact=True
            ).wait_for()
            assert lost_ack and len(replies) == 2 and replies[0] == replies[1]
            assert replies[0] is not None and json.loads(replies[0])["request_key"]
            assert await staff.get_by_role("list").get_by_text(reply_text, exact=True).count() == 1
            await staff.get_by_text("Staff · queued", exact=True).wait_for()
            await staff.get_by_role("button", name="Release to AI", exact=True).click()
            await staff.get_by_text("AI owns this conversation.", exact=True).wait_for()
            assert await staff.get_by_label("Human reply", exact=True).count() == 0
            await staff.reload()
            await staff.get_by_role(
                "button", name=f"{str(conversation)[:8]} · Telegram", exact=False
            ).click()
            await staff.get_by_text(reply_text, exact=True).wait_for()
            await staff.get_by_text("AI owns this conversation.", exact=True).wait_for()

            sandbox = await fetch(
                staff, "/console/v1/chat/conversations", {"request_key": str(uuid4())}
            )
            assert sandbox["status"] == 201
            sandbox_id = sandbox["body"]["id"]
            async with await psycopg.AsyncConnection.connect(
                migrated_dsn, autocommit=True
            ) as owner:
                await owner.execute(
                    "UPDATE conversations SET channel='telegram' WHERE id=%s", (sandbox_id,)
                )
            for suffix in ("", "/messages"):
                assert (
                    await fetch(staff, f"/console/v1/inbox/conversations/{sandbox_id}{suffix}")
                )["status"] == 404
            listed = await fetch(staff, "/console/v1/inbox/conversations")
            assert [item["id"] for item in listed["body"]["items"]] == [str(conversation)]

            for name in ("admin", "reader", "foreign"):
                member = await new_page(name)
                await member.goto(f"{w.origin}/portal/inbox")
                if name == "reader":
                    await member.get_by_text(
                        "Staff inbox access requires an administrator or staff role.", exact=True
                    ).wait_for()
                    assert await member.get_by_test_id("assistant-open").count() == 0
                    for suffix in ("", "/messages"):
                        assert (await fetch(member, f"{base}{suffix}"))["status"] == 403
                    assert (
                        await fetch(
                            member,
                            f"{base}/takeover",
                            {
                                "request_key": str(uuid4()),
                                "expected_version": 2,
                            },
                        )
                    )["status"] == 403
                elif name == "foreign":
                    await member.get_by_text(
                        "No customer conversations found.", exact=True
                    ).wait_for()
                    assert (await fetch(member, f"{base}/messages"))["status"] == 404
                else:
                    await member.get_by_role(
                        "button", name=f"{str(conversation)[:8]} · Telegram", exact=False
                    ).click()
                    await member.get_by_text(reply_text, exact=True).wait_for()

            platform = await new_page("platform")
            await platform.goto(f"{w.origin}/inbox")
            await platform.get_by_text("Select a tenant to open its inbox.", exact=True).wait_for()
            assert await platform.get_by_test_id("assistant-open").count() == 0
            assert (await fetch(platform, "/console/v1/inbox/conversations"))["status"] == 400
            await platform.get_by_label("Tenant").select_option(tenants[0])
            await platform.get_by_role(
                "button", name=f"{str(conversation)[:8]} · Telegram", exact=False
            ).click()
            await platform.get_by_text(reply_text, exact=True).wait_for()
            await platform.get_by_label("Tenant").select_option(tenants[1])
            await platform.get_by_text("No customer conversations found.", exact=True).wait_for()
            assert await platform.get_by_text(reply_text, exact=True).count() == 0

            approval_ids: dict[str, int] = {}
            async with await psycopg.AsyncConnection.connect(
                migrated_dsn, autocommit=True
            ) as owner:
                for decision in ("approve", "edit", "reject", "expire"):
                    approval_trace = uuid4()
                    await owner.execute(
                        "INSERT INTO ingress_events(client_id,endpoint_id,contact_id,"
                        "idempotency_key,payload_hash,trace_id,raw,status) "
                        "VALUES (%s,%s,%s,%s,'synthetic',%s,'{}','completed')",
                        (
                            tenants[0],
                            endpoint,
                            contact,
                            f"synthetic:{approval_trace}",
                            approval_trace,
                        ),
                    )
                    cursor = await owner.execute(
                        "INSERT INTO approvals(client_id,conversation_id,trace_id,draft,"
                        "reason,risk_score,ui,expires_at) "
                        "VALUES (%s,%s,%s,%s,'Synthetic review',0.5,'{}',now()+interval '1 hour') "
                        "RETURNING id",
                        (tenants[0], conversation, approval_trace, f"Synthetic {decision} draft"),
                    )
                    row = await cursor.fetchone()
                    assert row is not None
                    approval_ids[decision] = row[0]
            for decision, approval_id in approval_ids.items():
                # Approve and reject are confirmed on the shared proposal card, which repeats
                # the draft text; match the approval's own card only.
                card = staff.locator("article:not([data-testid='proposal-card'])").filter(
                    has=staff.get_by_text(f"Synthetic {decision} draft", exact=True)
                )
                await card.get_by_role("button", name="View conversation", exact=True).wait_for()
                if decision == "expire":
                    async with await psycopg.AsyncConnection.connect(
                        migrated_dsn, autocommit=True
                    ) as owner:
                        await owner.execute(
                            "UPDATE approvals SET expires_at=now()-interval '1 second' WHERE id=%s",
                            (approval_id,),
                        )
                    await card.get_by_role("button", name="Approve", exact=True).click()
                    await staff.get_by_test_id("proposal-confirm").click()
                    await staff.get_by_text("Approval expired.", exact=False).wait_for()
                elif decision == "edit":
                    await card.get_by_role("button", name="Edit", exact=True).click()
                    await card.get_by_role("textbox").fill("Synthetic edited answer")
                    await card.get_by_role("button", name="Queue edited reply", exact=True).click()
                else:
                    await card.get_by_role(
                        "button", name="Approve" if decision == "approve" else "Reject", exact=True
                    ).click()
                    await staff.get_by_test_id("proposal-confirm").click()
                    await (
                        staff.get_by_test_id("proposal-outcome")
                        .get_by_text("Your decision on the held reply was recorded.", exact=False)
                        .wait_for()
                    )
                await card.wait_for(state="detached")
                handled = await fetch(
                    staff,
                    f"/console/v1/inbox/approvals/{approval_id}/decide",
                    {
                        "request_key": str(uuid4()),
                        "decision": "approve",
                    },
                )
                assert handled["status"] == 200
                assert handled["body"]["outcome"] == "already_handled"
                assert handled["body"]["queued"] is False
            await staff.get_by_label("Include handled approvals", exact=True).check()
            await staff.get_by_text("Synthetic edit draft", exact=True).wait_for()

            async with await psycopg.AsyncConnection.connect(
                migrated_dsn, autocommit=True
            ) as owner:
                cursor = await owner.execute(
                    "SELECT owned_by,inbox_version,ai_muted_until FROM conversations WHERE id=%s",
                    (conversation,),
                )
                assert await cursor.fetchone() == ("ai", 2, None)
                cursor = await owner.execute(
                    "SELECT m.body,m.send_authority,d.status FROM messages m "
                    "JOIN outbound_deliveries d ON d.message_id=m.id "
                    "WHERE m.conversation_id=%s",
                    (conversation,),
                )
                deliveries = await cursor.fetchall()
                assert len(deliveries) == 5
                assert (reply_text, "human", "pending") in deliveries
                assert all(row[2] == "pending" for row in deliveries)
                assert ("Synthetic edited answer", "reviewed", "pending") in deliveries
                cursor = await owner.execute(
                    "SELECT status,count(*) FROM approvals WHERE client_id=%s "
                    "GROUP BY status ORDER BY status",
                    (tenants[0],),
                )
                assert await cursor.fetchall() == [
                    ("approved", 1),
                    ("edited", 1),
                    ("expired", 1),
                    ("rejected", 1),
                ]
                cursor = await owner.execute(
                    "SELECT count(*) FROM approval_audit WHERE client_id=%s", (tenants[0],)
                )
                assert await cursor.fetchone() == (4,)
                cursor = await owner.execute(
                    "SELECT action,count(*) FROM console_audit WHERE client_id=%s "
                    "AND action LIKE 'inbox.%%' GROUP BY action ORDER BY action",
                    (tenants[0],),
                )
                assert await cursor.fetchall() == [
                    ("inbox.release", 1),
                    ("inbox.reply", 1),
                    ("inbox.reply_decided", 8),
                    ("inbox.takeover", 1),
                ]
                cursor = await owner.execute(
                    "SELECT count(*) FROM console_inbox_actions WHERE client_id=%s",
                    (tenants[0],),
                )
                assert await cursor.fetchone() == (11,)
            assert not egress
            assert not page_errors
        finally:
            for context in reversed(contexts):
                await context.close()
            await browser.close()


async def test_packaged_hr_browser_acceptance(
    packaged_console: PackagedConsole,
    migrated_dsn: str,
) -> None:
    """Real browser/session/SQL evidence, with only one lost acknowledgement injected."""
    w = packaged_console
    member_password = "synthetic-hr-browser-password"
    emails = {
        name: f"hr-browser-{name}-{uuid4().hex}@example.test"
        for name in ("staff", "manager", "admin", "reader", "foreign")
    }
    tenants: list[str] = []
    users: dict[str, str] = {}
    employees = {name: uuid4() for name in ("staff", "manager", "foreign")}
    title = "Synthetic HR browser workshop"
    objective = "Practice bounded synthetic training"
    contexts: list[BrowserContext] = []
    egress: list[str] = []
    page_errors: list[str] = []
    draft_keys: list[str | None] = []
    draft_payloads: list[str | None] = []
    lost_ack = False

    with httpx.Client(base_url=w.origin, verify=w.tls, trust_env=False, timeout=30) as admin:

        def mutate(path: str, body: object) -> httpx.Response:
            csrf = admin.get("/console/v1/auth/csrf")
            assert csrf.status_code == 200
            return admin.post(
                path,
                json=body,
                headers={"Origin": w.origin, "X-CSRF-Token": csrf.json()["csrf_token"]},
            )

        assert (
            mutate(
                "/console/v1/auth/login",
                {
                    "email": "packaged-admin@example.test",
                    "password": PASSWORD,
                },
            ).status_code
            == 200
        )
        assert (
            mutate(
                "/console/v1/auth/password/change",
                {
                    "current_password": PASSWORD,
                    "new_password": CHANGED_PASSWORD,
                },
            ).status_code
            == 200
        )
        for label in ("alpha", "beta"):
            response = mutate(
                "/console/v1/admin/clients",
                {
                    "slug": f"hr-browser-{label}-{uuid4().hex[:12]}",
                    "name": f"Synthetic HR Browser {label}",
                },
            )
            assert response.status_code == 201, response.text
            tenants.append(response.json()["id"])
        # The HR API is package-gated. The foreign tenant needs hr too, so its cross-tenant
        # employee lookup reaches the HR service (404) instead of stopping at the gate (403).
        for tenant in tenants:
            activated = mutate(
                f"/console/v1/services/hr/activate?client_id={tenant}",
                {"reference": "synthetic-hr-browser"},
            )
            assert activated.status_code == 200, activated.text
        for name, email in emails.items():
            role = (
                "tenant_admin"
                if name == "admin"
                else ("read_only" if name == "reader" else "tenant_staff")
            )
            invitation = mutate(
                "/console/v1/admin/invites",
                {
                    "client_id": tenants[1] if name == "foreign" else tenants[0],
                    "email": email,
                    "display_name": f"Synthetic HR {name}",
                    "role": role,
                },
            )
            assert invitation.status_code == 201, invitation.text
            with httpx.Client(
                base_url=w.origin, verify=w.tls, trust_env=False, timeout=30
            ) as member:
                csrf = member.get("/console/v1/auth/csrf").json()["csrf_token"]
                accepted = member.post(
                    "/console/v1/invites/accept",
                    json={
                        "token": invitation.json()["token"],
                        "password": member_password,
                    },
                    headers={"Origin": w.origin, "X-CSRF-Token": csrf},
                )
                assert accepted.status_code == 200, accepted.text

    async with await psycopg.AsyncConnection.connect(migrated_dsn, autocommit=True) as owner:
        for name, email in emails.items():
            cursor = await owner.execute("SELECT id FROM console_users WHERE email=%s", (email,))
            row = await cursor.fetchone()
            assert row is not None
            users[name] = str(row[0])
        cursor = await owner.execute(
            "SELECT id FROM console_users WHERE email='packaged-admin@example.test'"
        )
        row = await cursor.fetchone()
        assert row is not None
        users["platform"] = str(row[0])
        await owner.execute(
            "INSERT INTO console_memberships(user_id,client_id,role) VALUES (%s,%s,'tenant_admin')",
            (users["platform"], tenants[0]),
        )
        await owner.execute(
            "INSERT INTO hr_grants(client_id,user_id,action,scope,fields) "
            "VALUES (%s,%s,'directory.read','hr_operator',ARRAY['display_name'])",
            (tenants[0], users["platform"]),
        )
        for name in ("manager", "staff", "foreign"):
            tenant = tenants[1] if name == "foreign" else tenants[0]
            await owner.execute(
                "INSERT INTO hr_employees(client_id,id,display_name,job_title,department,"
                "work_email,manager_id) VALUES (%s,%s,%s,'Trainer','Learning',%s,%s)",
                (
                    tenant,
                    employees[name],
                    f"Synthetic HR {name}",
                    f"private-{name}@example.test",
                    employees["manager"] if name == "staff" else None,
                ),
            )
            await owner.execute(
                "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
                (tenant, users[name], employees[name]),
            )
        for name in ("staff", "manager"):
            for action in (
                "directory.read",
                "training.read",
                "training.draft",
                "training.submit",
                "training.withdraw",
            ):
                fields = ["display_name"] if action == "directory.read" else []
                await owner.execute(
                    "INSERT INTO hr_grants(client_id,user_id,action,scope,fields) "
                    "VALUES (%s,%s,%s,'self',%s)",
                    (tenants[0], users[name], action, fields),
                )
        for action in ("directory.read", "training.read", "training.decide"):
            fields = (
                ["display_name", "job_title", "department", "work_email", "assigned_manager"]
                if action == "directory.read"
                else []
            )
            await owner.execute(
                "INSERT INTO hr_grants(client_id,user_id,action,scope,fields) "
                "VALUES (%s,%s,%s,'assigned_manager',%s)",
                (tenants[0], users["manager"], action, fields),
            )
        await owner.execute(
            "UPDATE hr_agent_settings SET enabled=true WHERE client_id=%s AND name='hr_training'",
            (tenants[0],),
        )

    async def hr_fetch(page: Page, path: str, body: object | None = None) -> dict:
        return await page.evaluate(
            """async ({path, body}) => {
                const headers = {};
                if (body !== null) {
                    const csrf = await (await fetch('/console/v1/auth/csrf')).json();
                    headers['X-CSRF-Token'] = csrf.csrf_token;
                    headers['Content-Type'] = 'application/json';
                    headers['Idempotency-Key'] = crypto.randomUUID();
                }
                const response = await fetch(path, {method: body === null ? 'GET' : 'POST',
                    headers, ...(body === null ? {} : {body: JSON.stringify(body)})});
                return {status: response.status, body: await response.json(),
                    cache: response.headers.get('cache-control')};
            }""",
            {"path": path, "body": body},
        )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()

        async def route_request(route: Route) -> None:
            nonlocal lost_ack
            target = urlsplit(route.request.url)
            if f"{target.scheme}://{target.netloc}" != w.origin:
                egress.append(f"{target.scheme}://{target.netloc}")
                await route.abort()
                return
            if (
                target.path == "/console/v1/hr/training-requests"
                and route.request.method == "POST"
                and "idempotency-key" in route.request.headers
            ):
                draft_keys.append(route.request.headers.get("idempotency-key"))
                draft_payloads.append(route.request.post_data)
                if not lost_ack:
                    response = await route.fetch()
                    assert response.status == 201
                    lost_ack = True
                    await route.abort("connectionreset")
                    return
            await route.continue_()

        async def new_page(name: str | None = None) -> Page:
            context = await browser.new_context(ignore_https_errors=True, service_workers="block")
            contexts.append(context)
            await context.route("**/*", route_request)
            page = await context.new_page()
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            if name is not None:
                await page.goto(f"{w.origin}/login")
                await page.get_by_label("Email", exact=True).fill(
                    "packaged-admin@example.test" if name == "platform" else emails[name]
                )
                await page.get_by_label("Password", exact=True).fill(
                    CHANGED_PASSWORD if name == "platform" else member_password
                )
                await page.get_by_role("button", name="Sign in", exact=True).click()
                await page.wait_for_url(
                    f"{w.origin}/" if name == "platform" else f"{w.origin}/portal"
                )
            return page

        try:
            anonymous = await new_page()
            await anonymous.goto(f"{w.origin}/portal/hr")
            await anonymous.wait_for_url(f"{w.origin}/login")
            assert (await hr_fetch(anonymous, "/console/v1/hr/context"))["status"] == 401
            assert (await hr_fetch(anonymous, "/console/v1/hr/guidance"))["status"] == 401

            # The Staff workspace page is gone; the assistant dock (button only here; no turn
            # is driven) is on every tab for staff and for admins naming a tenant.
            platform = await new_page("platform")
            await platform.goto(f"{w.origin}/hr")
            await platform.get_by_text("Select a tenant to continue.", exact=True).wait_for()
            assert await platform.get_by_test_id("assistant-open").count() == 0
            await platform.goto(f"{w.origin}/hr?section=directory&client_id={tenants[0]}")
            await platform.get_by_role("cell", name="Synthetic HR staff", exact=True).wait_for()
            assert (
                await platform.get_by_label("Tenant membership", exact=False).input_value()
                == (tenants[0])
            )
            await platform.get_by_test_id("assistant-open").wait_for()

            staff = await new_page("staff")
            await staff.goto(f"{w.origin}/portal/hr?section=directory")
            await staff.get_by_role("cell", name="Synthetic HR staff", exact=True).wait_for()
            assert await staff.get_by_text("private-staff@example.test", exact=True).count() == 0
            await staff.get_by_test_id("assistant-open").wait_for()
            projection = await hr_fetch(staff, f"/console/v1/hr/employees/{employees['staff']}")
            assert projection["status"] == 200 and projection["cache"] == "no-store"
            assert projection["body"]["values"] == {"display_name": "Synthetic HR staff"}
            csrf_denied = await staff.evaluate(
                """async () => (await fetch('/console/v1/hr/training-requests', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({title: 'Synthetic denied', learning_objective: 'No CSRF'})
                })).status"""
            )
            assert csrf_denied == 403
            assert await staff.get_by_text("private-staff@example.test", exact=True).count() == 0
            assert (
                await hr_fetch(
                    staff, f"/console/v1/hr/employees/{employees['foreign']}?client_id={tenants[1]}"
                )
            )["status"] == 403
            assert (await hr_fetch(staff, f"/console/v1/hr/employees/{employees['foreign']}"))[
                "status"
            ] == 404

            await staff.get_by_role("button", name="Training", exact=True).click()
            await staff.get_by_label("Training title", exact=True).fill(title)
            await staff.get_by_label("Learning objective", exact=True).fill(objective)
            await staff.get_by_role("button", name="Save draft", exact=True).click()
            await staff.get_by_text(
                "Refresh to check saved state before retrying.", exact=False
            ).wait_for()
            assert lost_ack and len(draft_keys) == 1
            await staff.get_by_role("button", name="Refresh", exact=True).click()
            article = staff.locator("article").filter(has=staff.get_by_role("heading", name=title))
            await article.get_by_text("draft · version 0", exact=True).wait_for()
            await staff.get_by_role("button", name="Save draft", exact=True).click()
            await staff.get_by_text(
                "Training draft saved. Submit it when ready.", exact=True
            ).wait_for()
            assert len(draft_keys) == 2 and draft_keys[0] is not None
            assert draft_keys[0] == draft_keys[1]
            assert draft_payloads[0] == draft_payloads[1]
            assert await article.count() == 1
            await article.get_by_role("button", name="submit", exact=True).click()
            await staff.get_by_role("button", name="Continue with submit", exact=True).click()
            # Submitting is confirmed on the shared proposal card; nothing changes before.
            proposal = staff.get_by_test_id("proposal-card")
            await proposal.get_by_text(f"Submit “{title}” for approval.", exact=True).wait_for()
            await article.get_by_text("draft · version 0", exact=True).wait_for()
            await staff.get_by_test_id("proposal-confirm").click()
            await staff.get_by_test_id("proposal-outcome").wait_for()
            await article.get_by_text("submitted · version 1", exact=True).wait_for()

            manager = await new_page("manager")
            await manager.goto(f"{w.origin}/portal/hr?section=training")
            manager_projection = await hr_fetch(
                manager, f"/console/v1/hr/employees/{employees['staff']}"
            )
            assert manager_projection["status"] == 200
            assert set(manager_projection["body"]["values"]) == {
                "display_name",
                "job_title",
                "department",
                "work_email",
                "assigned_manager",
            }
            await manager.get_by_label(
                "Use enabled staff agents for preparation.", exact=False
            ).check()
            reviewed = manager.locator("article").filter(
                has=manager.get_by_role("heading", name=title)
            )
            await reviewed.get_by_text("submitted · version 1", exact=True).wait_for()
            await reviewed.get_by_role("button", name="approve", exact=True).click()
            await manager.get_by_label("Decision reason", exact=True).fill(
                "Synthetic manager review"
            )
            await manager.get_by_role("button", name="Continue with approve", exact=True).click()
            await manager.get_by_role(
                "heading", name="Confirm the prepared training decision"
            ).wait_for()
            await reviewed.get_by_text("submitted · version 1", exact=True).wait_for()
            await manager.get_by_role("button", name="Confirm decision", exact=True).click()
            await reviewed.get_by_text("approved · version 2", exact=True).wait_for()
            await staff.reload()
            await staff.get_by_role("button", name="Training", exact=True).click()
            await article.get_by_text("approved · version 2", exact=True).wait_for()

            for name in ("admin", "reader", "foreign"):
                denied = await new_page(name)
                await denied.goto(f"{w.origin}/portal/hr")
                response = await hr_fetch(denied, f"/console/v1/hr/employees/{employees['staff']}")
                assert response["status"] == 404
                assert "private-staff" not in json.dumps(response["body"])
                if name == "reader":
                    assert (
                        await denied.get_by_role("button", name="Save draft", exact=True).count()
                        == 0
                    )
                    assert (
                        await hr_fetch(
                            denied,
                            "/console/v1/hr/training-requests",
                            {
                                "title": title,
                                "learning_objective": objective,
                            },
                        )
                    )["status"] == 403

            async with await psycopg.AsyncConnection.connect(
                migrated_dsn, autocommit=True
            ) as owner:
                cursor = await owner.execute(
                    "SELECT status,version,title,learning_objective FROM hr_training_requests "
                    "WHERE client_id=%s",
                    (tenants[0],),
                )
                assert await cursor.fetchall() == [("approved", 2, title, objective)]
                cursor = await owner.execute(
                    "SELECT action,count(*) FROM hr_training_receipts WHERE client_id=%s "
                    "GROUP BY action ORDER BY action",
                    (tenants[0],),
                )
                assert await cursor.fetchall() == [("approve", 1), ("save_draft", 1), ("submit", 1)]
                cursor = await owner.execute(
                    "SELECT count(*),bool_and(consumed_at IS NOT NULL) "
                    "FROM hr_agent_confirmations WHERE client_id=%s",
                    (tenants[0],),
                )
                assert await cursor.fetchone() == (1, True)
                cursor = await owner.execute(
                    "SELECT count(*) FROM hr_training_audit WHERE client_id=%s",
                    (tenants[0],),
                )
                assert await cursor.fetchone() == (3,)
                await owner.execute(
                    "DELETE FROM hr_grants WHERE client_id=%s AND user_id=%s",
                    (tenants[0], users["manager"]),
                )
            revoked = await hr_fetch(manager, f"/console/v1/hr/employees/{employees['staff']}")
            assert revoked["status"] == 404
            assert "private-staff" not in json.dumps(revoked["body"])
            assert not egress
            assert not page_errors
        finally:
            for context in reversed(contexts):
                await context.close()
            await browser.close()
