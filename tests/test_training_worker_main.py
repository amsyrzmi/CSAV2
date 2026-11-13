"""The training worker process: idle while video is off, and what it refuses and checks at start."""

from __future__ import annotations

import asyncio
import base64
import contextlib
from pathlib import Path
from typing import Any

import pytest
from apps.training_worker import main as training_main
from csa.core.settings import Settings
from csa.storage.factory import Stores
from csa.storage.local import LocalObjectStore

# Settings looks for /run/secrets even when told not to; the folder is absent off Docker.
pytestmark = pytest.mark.filterwarnings("ignore:directory .* does not exist:UserWarning")

SPEECH = "https://speech.example.test/openai/v1/audio/transcriptions"


def make_settings(**overrides: str) -> Settings:
    env = {
        "ENV": "ci",
        "DATABASE_URL": "postgresql://csa_app:pw@localhost:5433/csa",
        "LG_DATABASE_URL": "postgresql://csa_app:pw@localhost:5433/csa",
        "GROQ_API_KEY": "synthetic",
        "PUBLIC_BASE_URL": "https://example.test",
        "CREDENTIALS_KEY": "base64:" + base64.b64encode(b"\x01" * 32).decode("ascii"),
    }
    env.update(overrides)
    return Settings(_env_file=None, _secrets_dir=None, **env)  # type: ignore[call-arg]


class RecordingStore(LocalObjectStore):
    """A store that records whether it was checked."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.checks = 0

    def check(self) -> None:
        self.checks += 1


class Stop(Exception):
    """Raised where the test wants ``main`` to stop: at the first pool."""


def refuse_pools(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    opened: list[str] = []

    def build_pool(conninfo: str, **kwargs: Any) -> Any:
        opened.append(conninfo)
        raise Stop

    monkeypatch.setattr(training_main.db, "build_pool", build_pool)
    return opened


def use_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(training_main, "get_settings", lambda: settings)


async def test_worker_idles_with_the_flag_off_and_touches_liveness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alive = tmp_path / "training-worker.alive"
    monkeypatch.setattr(training_main, "TRAINING_WORKER_ALIVE_PATH", alive)
    use_settings(monkeypatch, make_settings())

    def no_stores(settings: Settings) -> Stores:
        raise AssertionError("the worker must not build stores while video is off")

    monkeypatch.setattr(training_main, "build_stores", no_stores)
    opened = refuse_pools(monkeypatch)

    task = asyncio.create_task(training_main.main())
    try:
        for _ in range(200):
            if alive.exists() or task.done():
                break
            await asyncio.sleep(0.02)
        assert not task.done()
        assert alive.exists()
        first = alive.stat().st_mtime_ns
        assert opened == []
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert first > 0
    assert training_main.TRAINING_WORKER_ALIVE_PATH == alive


async def test_worker_refuses_to_start_without_a_speech_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_settings(
        monkeypatch,
        make_settings(TRAINING_VIDEO_ENABLED="true", TRAINING_UPLOAD_ROOT=str(tmp_path / "t")),
    )
    opened = refuse_pools(monkeypatch)
    with pytest.raises(RuntimeError, match="VOICE_STT_ENDPOINT"):
        await training_main.main()
    assert opened == []

    # Nor without the training store.
    use_settings(
        monkeypatch, make_settings(TRAINING_VIDEO_ENABLED="true", VOICE_STT_ENDPOINT=SPEECH)
    )
    with pytest.raises(RuntimeError, match="training store"):
        await training_main.main()
    assert opened == []


async def test_worker_builds_and_checks_only_the_training_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb, hr = RecordingStore(tmp_path / "kb"), RecordingStore(tmp_path / "hr")
    training = RecordingStore(tmp_path / "training")
    monkeypatch.setattr(
        training_main, "build_stores", lambda settings: Stores(kb=kb, hr=hr, training=training)
    )
    use_settings(
        monkeypatch,
        make_settings(
            TRAINING_VIDEO_ENABLED="true",
            VOICE_STT_ENDPOINT=SPEECH,
            TRAINING_UPLOAD_ROOT=str(tmp_path / "training"),
        ),
    )
    opened = refuse_pools(monkeypatch)

    with pytest.raises(Stop):
        await training_main.main()

    assert (kb.checks, hr.checks, training.checks) == (0, 0, 1)
    # The store is checked before any pool is opened.
    assert len(opened) == 1
    assert (
        await training_main.prepare_training_store(
            make_settings(TRAINING_UPLOAD_ROOT=str(tmp_path / "training"))
        )
        is training
    )
    assert training.checks == 2
