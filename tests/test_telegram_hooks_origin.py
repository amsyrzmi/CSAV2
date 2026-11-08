"""The origin Telegram webhooks are registered under: hooks hostname only, never the console."""

from __future__ import annotations

from pathlib import Path

from tests.test_console_identity_config import configured
from tests.test_staging_pipeline import render


def test_the_console_origin_is_never_the_hooks_origin() -> None:
    assert configured().hooks_origin is None


def test_the_whatsapp_callback_origin_serves_when_no_telegram_one_is_set() -> None:
    settings = configured(WHATSAPP_CALLBACK_BASE_URL="https://hooks.example.test/")
    assert settings.hooks_origin == "https://hooks.example.test"


def test_the_telegram_hooks_origin_wins_and_loses_its_trailing_slash() -> None:
    settings = configured(
        HOOKS_PUBLIC_BASE_URL="https://hooks.telegram.example.test/",
        WHATSAPP_CALLBACK_BASE_URL="https://hooks.example.test",
    )
    assert settings.hooks_origin == "https://hooks.telegram.example.test"


def test_the_staging_gateway_gets_the_hooks_origin_from_the_rollout_variable(
    tmp_path: Path,
) -> None:
    gateway = render(tmp_path, "staging-approved", "pipeline-approved")["services"]["gateway"]
    assert gateway["environment"]["HOOKS_PUBLIC_BASE_URL"] == "https://hooks.invalid"
