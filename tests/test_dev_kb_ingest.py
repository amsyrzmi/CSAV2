"""The acceptance worker cannot start the full runtime or target another database."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from csa.core.settings import Settings
from scripts.dev_kb_ingest import validate


def config(**changes) -> Settings:
    values = {
        "ENV": "dev",
        "DATABASE_URL": "postgresql://csa_app@127.0.0.1:5434/csa",
        "KB_INGEST_ENABLED": True,
        "KB_UPLOAD_ROOT": Path("/tmp/synthetic-kb"),
        "TEI_ENDPOINT": "http://127.0.0.1:18082",
    }
    return Mock(spec=Settings, **(values | changes))


def test_isolated_worker_config() -> None:
    validate(config())
    validate(config(TEI_ENDPOINT=None))


@pytest.mark.parametrize(
    "changes",
    [
        {"ENV": "prod"},
        {"DATABASE_URL": "postgresql://csa_app@127.0.0.1:5432/csa"},
        {"DATABASE_URL": "postgresql://csa@127.0.0.1:5434/csa"},
        {"DATABASE_URL": "postgresql://csa_app@remote.example.test:5434/csa"},
        {"KB_INGEST_ENABLED": False},
        {"KB_UPLOAD_ROOT": None},
        {"KB_UPLOAD_ROOT": Path(".")},
        {"KB_UPLOAD_ROOT": Path("/")},
        {"TEI_ENDPOINT": "https://remote.example.test"},
        {"TEI_ENDPOINT": "http://user:synthetic@127.0.0.1:18082"},
    ],
)
def test_unsafe_worker_config_rejected(changes) -> None:
    with pytest.raises(ValueError):
        validate(config(**changes))
