"""The owner's Jev key script for staging (temporary directories only, synthetic values)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from pydantic import SecretStr
from scripts import staging_jev_key, staging_secrets

UID = os.getuid()
KEY = "synthetic-jev-key-0123456789abcdef"


def _layout(tmp_path: Path) -> Path:
    target = tmp_path / "secrets"
    staging_secrets.create(target, UID)
    return target


def test_the_key_file_is_owner_only_exact_and_passes_check(tmp_path: Path) -> None:
    target = _layout(tmp_path)
    # Surrounding whitespace and a pasted newline are stripped; nothing is added.
    assert staging_jev_key.provision(target, SecretStr(f"  {KEY}\n")) == "pipeline/staging_jev_key"
    key = target / "pipeline/staging_jev_key"
    info = key.lstat()
    assert stat.S_ISREG(info.st_mode)
    assert stat.S_IMODE(info.st_mode) == 0o400
    assert info.st_uid == UID
    assert key.read_bytes() == KEY.encode()
    assert stat.S_IMODE((target / "pipeline").stat().st_mode) == 0o700
    assert staging_secrets.check(target, UID) == []


def test_an_existing_key_is_never_overwritten(tmp_path: Path) -> None:
    target = _layout(tmp_path)
    staging_jev_key.provision(target, SecretStr(KEY))
    with pytest.raises(FileExistsError) as caught:
        staging_jev_key.provision(target, SecretStr("another-synthetic-jev-key-value"))
    assert "another-synthetic" not in str(caught.value)
    assert (target / "pipeline/staging_jev_key").read_text() == KEY


def test_a_symlink_in_place_of_the_key_is_refused_and_not_followed(tmp_path: Path) -> None:
    target = _layout(tmp_path)
    (target / "pipeline").mkdir(mode=0o700)
    elsewhere = tmp_path / "elsewhere"
    (target / "pipeline/staging_jev_key").symlink_to(elsewhere)
    with pytest.raises(FileExistsError):
        staging_jev_key.provision(target, SecretStr(KEY))
    assert not elsewhere.exists()


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "\n",
        "disabled",
        staging_secrets.DISABLED_MODEL_KEY,
        "two words",
        "line\nbreak",
    ],
)
def test_an_empty_placeholder_or_spaced_key_is_refused(tmp_path: Path, value: str) -> None:
    target = _layout(tmp_path)
    with pytest.raises(staging_secrets.ProvisionError) as caught:
        staging_jev_key.provision(target, SecretStr(value))
    if value.strip():
        assert value.strip() not in str(caught.value)
    assert not (target / "pipeline/staging_jev_key").exists()


def test_the_layout_must_pass_check_first(tmp_path: Path) -> None:
    target = _layout(tmp_path)
    (target / "runtime").chmod(0o755)
    with pytest.raises(staging_secrets.ProvisionError, match="must pass check"):
        staging_jev_key.provision(target, SecretStr(KEY))
    assert not (target / "pipeline/staging_jev_key").exists()
    # A directory that is no layout at all is refused the same way.
    with pytest.raises(staging_secrets.ProvisionError):
        staging_jev_key.provision(tmp_path / "missing", SecretStr(KEY))


def test_a_loose_pipeline_directory_is_refused(tmp_path: Path) -> None:
    target = _layout(tmp_path)
    (target / "pipeline").mkdir(mode=0o755)
    (target / "pipeline").chmod(0o755)
    with pytest.raises(staging_secrets.ProvisionError, match="private"):
        staging_jev_key.provision(target, SecretStr(KEY))
    assert not (target / "pipeline/staging_jev_key").exists()


def test_main_prompts_hidden_and_prints_only_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _layout(tmp_path)
    prompts: list[str] = []

    def hidden(prompt: str = "") -> str:
        prompts.append(prompt)
        return KEY + "\n"

    monkeypatch.setattr(staging_jev_key.getpass, "getpass", hidden)
    assert staging_jev_key.main(["--secret-dir", str(target)]) == 0
    assert len(prompts) == 1
    out = capsys.readouterr()
    assert "pipeline/staging_jev_key" in out.out
    assert KEY not in out.out + out.err
    # A second run refuses, still without the value.
    assert staging_jev_key.main(["--secret-dir", str(target)]) == 1
    out = capsys.readouterr()
    assert "not written" in out.out + out.err
    assert KEY not in out.out + out.err


def test_main_with_no_input_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _layout(tmp_path)

    def no_input(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr(staging_jev_key.getpass, "getpass", no_input)
    assert staging_jev_key.main(["--secret-dir", str(target)]) == 1
    assert not (target / "pipeline/staging_jev_key").exists()
