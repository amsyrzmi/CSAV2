"""Production Compose overlay: S3 settings, memory limits and key separation.

Renders the four-file stack with synthetic values (nothing starts) and feeds each service's
rendered environment to the real ``Settings`` class, so a setting Compose silently kept from an
earlier file is caught here and not by a gateway that refuses to start on the host.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from csa.core.settings import Settings
from scripts import staging_secrets

from tests.test_staging_compose import _compose_env, _make_synthetic_secrets

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "compose.staging-local-proposal.yml",
    "compose.staging-shared-caddy.yml",
    "compose.staging-pipeline.yml",
    "compose.production.yml",
)
PROFILES = ("staging-approved", "pipeline-approved", "dedicated-connector-approved")
S3_NAMES = {"S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"}

pytestmark = [
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI is unavailable"),
    pytest.mark.filterwarnings("ignore:directory .* does not exist:UserWarning"),
]


def _secret_dir(tmp_path: Path) -> Path:
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir(mode=0o700)
    _make_synthetic_secrets(secret_dir)
    (secret_dir / "pipeline").mkdir(mode=0o700)
    (secret_dir / "pipeline/staging_model_key").write_text("SYNTHETIC-ONLY")
    (secret_dir / "pipeline/staging_telegram_bot_token").write_text("SYNTHETIC-ONLY")
    for name in staging_secrets.WHATSAPP_FILES:
        (secret_dir / name).write_text(staging_secrets.WHATSAPP_DISABLED)
    for name in staging_secrets.TIKTOK_FILES:
        (secret_dir / name).write_text(staging_secrets.TIKTOK_DISABLED)
    (secret_dir / "storage").mkdir(mode=0o700)
    for name in (
        "s3_app_access_key_id",
        "s3_app_secret_access_key",
        "s3_worker_access_key_id",
        "s3_worker_secret_access_key",
    ):
        (secret_dir / "storage" / name).write_text("SYNTHETIC-ONLY")
    return secret_dir


def _compose(tmp_path: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    command = ["docker", "compose", "--env-file", "/dev/null"]
    for name in FILES:
        command += ["--file", str(ROOT / "deploy" / name)]
    for profile in PROFILES:
        command += ["--profile", profile]
    command += ["config", "--format", "json"]
    env = _compose_env(_secret_dir(tmp_path)) | {
        "CSA_STAGING_HOOKS_ORIGIN": "https://hooks.invalid"
    }
    env |= env_extra
    return subprocess.run(command, env=env, capture_output=True, text=True, check=False)


def render(tmp_path: Path) -> dict:
    result = _compose(
        tmp_path,
        {"CSA_PROD_S3_BUCKET": "bucket-invalid", "CSA_PROD_S3_REGION": "ap-southeast-1"},
    )
    assert result.returncode == 0, result.stderr
    assert "SYNTHETIC-ONLY" not in result.stdout
    return json.loads(result.stdout)["services"]


def settings_for(service: dict) -> Settings:
    """Build Settings from a rendered service the way the container would see it."""
    env = {k: v for k, v in service.get("environment", {}).items() if k in Settings.model_fields}
    targets = {s["target"] for s in service.get("secrets", [])}
    env.update(
        {
            "DATABASE_URL": "postgresql://csa_app:pw@localhost:5433/csa",
            "LG_DATABASE_URL": "postgresql://csa_app:pw@localhost:5433/csa",
            "GROQ_API_KEY": "synthetic",
            "CREDENTIALS_KEY": "base64:" + base64.b64encode(b"\x01" * 32).decode("ascii"),
        }
    )
    env.update({name: "synthetic" for name in S3_NAMES & targets})
    return Settings(_env_file=None, _secrets_dir=None, **env)  # type: ignore[call-arg]


def test_each_service_settings_validate_with_the_real_settings_class(tmp_path: Path) -> None:
    services = render(tmp_path)
    for name in ("gateway", "worker"):
        settings = settings_for(services[name])
        assert settings.STORAGE_BACKEND == "s3"
        assert settings.S3_BUCKET == "bucket-invalid"
        assert settings.KB_UPLOAD_ROOT is None
        assert settings.HR_UPLOAD_ROOT is None
    delivery = settings_for(services["delivery"])
    assert delivery.STORAGE_BACKEND == "local"
    assert delivery.KB_UPLOAD_ROOT is None
    assert delivery.HR_UPLOAD_ROOT is None


def test_no_s3_key_is_ever_an_environment_variable(tmp_path: Path) -> None:
    # An environment variable would win over the secret file (docs/OBJECT_STORAGE.md).
    for name, service in render(tmp_path).items():
        assert not S3_NAMES & set(service.get("environment", {})), name


def test_s3_keys_are_split_by_service(tmp_path: Path) -> None:
    services = render(tmp_path)

    def sources(name: str) -> set[str]:
        return {s["source"] for s in services[name].get("secrets", []) if s["target"] in S3_NAMES}

    assert sources("gateway") == {"prod_s3_app_access_key_id", "prod_s3_app_secret_access_key"}
    assert sources("worker") == {
        "prod_s3_worker_access_key_id",
        "prod_s3_worker_secret_access_key",
    }
    assert sources("delivery") == set()
    assert sources("frontend") == set()


def test_no_service_mounts_a_local_upload_volume(tmp_path: Path) -> None:
    for name, service in render(tmp_path).items():
        sources = {v.get("source") for v in service.get("volumes", [])}
        assert not sources & {"staging-uploads", "staging-hr-uploads"}, name


def test_two_workers_and_memory_limits_protect_the_database(tmp_path: Path) -> None:
    services = render(tmp_path)
    assert services["worker"]["deploy"]["replicas"] == 2
    for name in (
        "staging-db",
        "gateway",
        "worker",
        "delivery",
        "frontend",
        "staging-embeddings",
        "cloudflared",
    ):
        assert int(services[name]["mem_limit"]) > 0, name
    # Lowest score is chosen last by the OOM killer: Postgres, then everything else.
    ranked = ("staging-db", "gateway", "worker", "delivery", "frontend", "staging-embeddings")
    scores = {name: services[name].get("oom_score_adj", 0) for name in ranked}
    assert scores["staging-db"] == min(scores.values()) < 0
    assert scores["worker"] == max(scores.values())


def test_gateway_reaches_the_connector_and_the_database_is_tuned(tmp_path: Path) -> None:
    services = render(tmp_path)
    assert "edge" in services["gateway"]["networks"]
    assert "max_connections=60" in services["staging-db"]["command"]


def test_the_database_image_can_be_named_by_the_operator(tmp_path: Path) -> None:
    result = _compose(
        tmp_path,
        {
            "CSA_PROD_S3_BUCKET": "b",
            "CSA_PROD_S3_REGION": "r",
            "CSA_PROD_DB_IMAGE": "example.invalid/db:local",
        },
    )
    assert result.returncode == 0, result.stderr
    image = json.loads(result.stdout)["services"]["staging-db"]["image"]
    assert image == "example.invalid/db:local"


def test_a_missing_bucket_or_region_stops_compose(tmp_path: Path) -> None:
    for index, extra in enumerate(({"CSA_PROD_S3_REGION": "r"}, {"CSA_PROD_S3_BUCKET": "b"})):
        folder = tmp_path / f"case{index}"
        folder.mkdir()
        result = _compose(folder, extra)
        assert result.returncode != 0
        assert "S3" in result.stderr
