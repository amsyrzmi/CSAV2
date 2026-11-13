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
from pydantic import SecretStr
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
# The training worker's own key: made by the owner before video is switched on.
TRAINING_KEY_FILES = ("s3_training_access_key_id", "s3_training_secret_access_key")

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


def _compose(
    tmp_path: Path, env_extra: dict[str, str], *, training_key_files: bool = False
) -> subprocess.CompletedProcess[str]:
    command = ["docker", "compose", "--env-file", "/dev/null"]
    for name in FILES:
        command += ["--file", str(ROOT / "deploy" / name)]
    for profile in PROFILES:
        command += ["--profile", profile]
    command += ["config", "--format", "json"]
    secret_dir = _secret_dir(tmp_path)
    if training_key_files:
        for name in TRAINING_KEY_FILES:
            (secret_dir / "storage" / name).write_text("SYNTHETIC-ONLY")
    env = _compose_env(secret_dir) | {"CSA_STAGING_HOOKS_ORIGIN": "https://hooks.invalid"}
    env |= env_extra
    return subprocess.run(command, env=env, capture_output=True, text=True, check=False)


def render(
    tmp_path: Path, env_extra: dict[str, str] | None = None, *, training_key_files: bool = False
) -> dict:
    result = _compose(
        tmp_path,
        {"CSA_PROD_S3_BUCKET": "bucket-invalid", "CSA_PROD_S3_REGION": "ap-southeast-1"}
        | (env_extra or {}),
        training_key_files=training_key_files,
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
    for name in ("gateway", "worker", "training-worker"):
        settings = settings_for(services[name])
        assert settings.STORAGE_BACKEND == "s3"
        assert settings.S3_BUCKET == "bucket-invalid"
        assert settings.KB_UPLOAD_ROOT is None
        assert settings.HR_UPLOAD_ROOT is None
        assert settings.TRAINING_UPLOAD_ROOT is None
    delivery = settings_for(services["delivery"])
    assert delivery.STORAGE_BACKEND == "local"
    assert delivery.KB_UPLOAD_ROOT is None
    assert delivery.HR_UPLOAD_ROOT is None
    assert delivery.TRAINING_UPLOAD_ROOT is None


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
    assert sources("training-worker") == {
        "prod_s3_training_disabled_access_key_id",
        "prod_s3_training_disabled_secret_access_key",
    }
    assert sources("delivery") == set()
    assert sources("frontend") == set()


def test_no_service_mounts_a_local_upload_volume(tmp_path: Path) -> None:
    for name, service in render(tmp_path).items():
        sources = {v.get("source") for v in service.get("volumes", [])}
        assert not sources & {
            "staging-uploads",
            "staging-hr-uploads",
            "staging-training-uploads",
        }, name


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
        "training-worker",
    ):
        assert int(services[name]["mem_limit"]) > 0, name
    # Lowest score is chosen last by the OOM killer: Postgres, then everything else. The training
    # worker goes first (its video job is resumable), then the workers.
    ranked = (
        "staging-db",
        "gateway",
        "worker",
        "delivery",
        "frontend",
        "staging-embeddings",
        "training-worker",
    )
    scores = {name: services[name].get("oom_score_adj", 0) for name in ranked}
    assert scores["staging-db"] == min(scores.values()) < 0
    assert scores["training-worker"] == max(scores.values())
    others = {name: score for name, score in scores.items() if name != "training-worker"}
    assert scores["worker"] == max(others.values()) < scores["training-worker"]


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


def test_tuning_values_come_from_the_host_env_file_without_a_code_change(tmp_path: Path) -> None:
    services = render(
        tmp_path,
        {
            "CSA_PROD_WORKER_REPLICAS": "1",
            "CSA_PROD_MEM_WORKER": "512m",
            "CSA_PROD_MEM_DB": "1g",
            "CSA_PROD_PG_SHARED_BUFFERS": "256MB",
            "CSA_PROD_PG_MAX_CONNECTIONS": "80",
        },
    )
    assert services["worker"]["deploy"]["replicas"] == 1
    assert int(services["worker"]["mem_limit"]) == 512 * 1024 * 1024
    assert int(services["staging-db"]["mem_limit"]) == 1024**3
    assert "shared_buffers=256MB" in services["staging-db"]["command"]
    assert "max_connections=80" in services["staging-db"]["command"]


def test_the_model_url_defaults_to_groq_and_a_stub_reaches_gateway_and_worker(
    tmp_path: Path,
) -> None:
    groq = "https://api.groq.com/openai/v1/chat/completions"
    services = render(tmp_path)
    for name in ("gateway", "worker"):
        assert services[name]["environment"]["GROQ_CHAT_URL"] == groq
        settings_for(services[name])
    assert "GROQ_CHAT_URL" not in services["delivery"].get("environment", {})
    stub = "http://model-stub:8080/v1/chat/completions"
    folder = tmp_path / "stubbed"
    folder.mkdir()
    stubbed = render(folder, {"CSA_PROD_GROQ_CHAT_URL": stub})
    for name in ("gateway", "worker"):
        assert stubbed[name]["environment"]["GROQ_CHAT_URL"] == stub
        assert str(settings_for(stubbed[name]).GROQ_CHAT_URL) == stub


def test_training_worker_holds_only_the_training_key(tmp_path: Path) -> None:
    # The owner made the key: its two files exist and the switch names them.
    services = render(
        tmp_path, {"CSA_PROD_S3_TRAINING_KEY": "prod_s3_training"}, training_key_files=True
    )

    def sources(name: str) -> set[str]:
        return {s["source"] for s in services[name].get("secrets", []) if s["target"] in S3_NAMES}

    assert sources("training-worker") == {
        "prod_s3_training_access_key_id",
        "prod_s3_training_secret_access_key",
    }
    # The ingest workers keep the kb/ key; no other service receives the training key.
    assert sources("worker") == {
        "prod_s3_worker_access_key_id",
        "prod_s3_worker_secret_access_key",
    }
    assert sources("gateway") == {"prod_s3_app_access_key_id", "prod_s3_app_secret_access_key"}
    for name, service in services.items():
        if name != "training-worker":
            assert not {s["source"] for s in service.get("secrets", [])} & {
                "prod_s3_training_access_key_id",
                "prod_s3_training_secret_access_key",
            }, name
    # Everything else the training worker holds is the staging worker's: database, credentials,
    # model.
    others = {
        s["target"]: s["source"]
        for s in services["training-worker"]["secrets"]
        if s["target"] not in S3_NAMES
    }
    assert others == {
        "DATABASE_URL": "staging_domain_dsn",
        "LG_DATABASE_URL": "staging_owner_dsn",
        "CREDENTIALS_KEY": "staging_credentials_key",
        "GROQ_API_KEY": "staging_model_key",
    }
    settings = settings_for(services["training-worker"])
    assert settings.STORAGE_BACKEND == "s3"
    assert settings.TRAINING_UPLOAD_ROOT is None


def test_training_worker_mounts_nothing_and_is_killed_before_the_workers(tmp_path: Path) -> None:
    services = render(tmp_path)
    training = services["training-worker"]
    assert training.get("volumes", []) == []
    assert int(training["mem_limit"]) == 256 * 1024**2
    assert training["oom_score_adj"] == 600
    assert training["oom_score_adj"] > services["worker"]["oom_score_adj"] == 500
    assert training.get("deploy", {}).get("replicas", 1) == 1
    env = training["environment"]
    assert env["STORAGE_BACKEND"] == "s3"
    assert env["TRAINING_UPLOAD_ROOT"] == ""
    assert env["GROQ_CHAT_URL"] == "https://api.groq.com/openai/v1/chat/completions"
    assert env["VOICE_STT_ENDPOINT"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    # The limit is tunable from the host env file like every other.
    folder = tmp_path / "tuned"
    folder.mkdir()
    tuned = render(folder, {"CSA_PROD_MEM_TRAINING_WORKER": "320m"})
    assert int(tuned["training-worker"]["mem_limit"]) == 320 * 1024**2


def test_compose_renders_without_the_training_key_while_the_flag_is_off(tmp_path: Path) -> None:
    result = _compose(
        tmp_path, {"CSA_PROD_S3_BUCKET": "bucket-invalid", "CSA_PROD_S3_REGION": "ap-southeast-1"}
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "secrets" / "storage" / TRAINING_KEY_FILES[0]).exists()
    config = json.loads(result.stdout)
    training = config["services"]["training-worker"]
    assert training["environment"]["TRAINING_VIDEO_ENABLED"] == "false"
    # Both S3 names come from the disabled placeholder; the real key files are not needed.
    placeholders = {
        s["target"]: s["source"] for s in training["secrets"] if s["target"] in S3_NAMES
    }
    assert placeholders == {
        "S3_ACCESS_KEY_ID": "prod_s3_training_disabled_access_key_id",
        "S3_SECRET_ACCESS_KEY": "prod_s3_training_disabled_secret_access_key",
    }
    for name in placeholders.values():
        assert Path(config["secrets"][name]["file"]).name == "staging_disabled_model_key"
    assert "prod_s3_training_access_key_id" not in config["secrets"]
    assert "prod_s3_training_secret_access_key" not in config["secrets"]


async def test_the_training_worker_refuses_to_start_on_the_placeholder_key(tmp_path: Path) -> None:
    from apps.training_worker.main import prepare_training_store

    services = render(tmp_path, {"CSA_STAGING_TRAINING_VIDEO": "true"})
    service = services["training-worker"]
    assert service["environment"]["TRAINING_VIDEO_ENABLED"] == "true"
    # The container reads both S3 names from the placeholder file.
    settings = settings_for(service).model_copy(
        update={
            "S3_ACCESS_KEY_ID": SecretStr(staging_secrets.DISABLED_MODEL_KEY),
            "S3_SECRET_ACCESS_KEY": SecretStr(staging_secrets.DISABLED_MODEL_KEY),
        }
    )
    assert settings.TRAINING_VIDEO_ENABLED is True
    with pytest.raises(RuntimeError, match="training worker's own S3 key"):
        await prepare_training_store(settings)
