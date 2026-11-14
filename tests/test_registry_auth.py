"""The optional read-only registry login of the rollout scripts (``scripts.registry_auth``).

Every value here is synthetic. The file content is a credential: no assertion message may carry
it, so the checks below compare it with ``not in`` and never print it.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest
from scripts import registry_auth
from scripts.rollout_targets import PRODUCTION, STAGING
from scripts.staging_rollout import RolloutError

# A docker config with one synthetic login; "c3ludGhldGlj" is base64 of "synthetic".
LOGIN = b'{"auths": {"ghcr.io": {"auth": "c3ludGhldGlj"}}}\n'
SECRETISH = "c3ludGhldGlj"


def docker_config(tmp_path: Path) -> Path:
    directory = tmp_path / "docker"
    directory.mkdir(mode=0o700)
    return directory


def login_file(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / "registry" / "config.json"
    path.parent.mkdir()
    path.write_bytes(content)
    return path


def test_an_absent_file_means_anonymous_and_writes_nothing(tmp_path: Path) -> None:
    directory = docker_config(tmp_path)
    assert registry_auth.install(directory, tmp_path / "registry" / "config.json") is False
    assert os.listdir(directory) == []


def test_a_valid_file_is_copied_byte_for_byte_with_mode_0600(tmp_path: Path) -> None:
    directory = docker_config(tmp_path)
    source = login_file(tmp_path, LOGIN)
    assert registry_auth.install(directory, source) is True
    assert os.listdir(directory) == ["config.json"]
    copied = directory / "config.json"
    assert stat.S_IMODE(copied.stat().st_mode) == 0o600
    assert copied.read_bytes() == LOGIN
    assert source.read_bytes() == LOGIN  # the source is left as it was


def test_the_copy_is_0600_whatever_the_source_mode(tmp_path: Path) -> None:
    directory = docker_config(tmp_path)
    source = login_file(tmp_path, LOGIN)
    source.chmod(0o644)
    assert registry_auth.install(directory, source) is True
    assert stat.S_IMODE((directory / "config.json").stat().st_mode) == 0o600


def test_the_copy_goes_away_with_the_docker_config_directory(tmp_path: Path) -> None:
    directory = docker_config(tmp_path)
    assert registry_auth.install(directory, login_file(tmp_path, LOGIN)) is True
    shutil.rmtree(directory)
    assert not directory.exists()


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not json " + SECRETISH.encode(),
        b"[]",
        b'["' + SECRETISH.encode() + b'"]',
        b'{"credsStore": "' + SECRETISH.encode() + b'"}',
        b'{"auths": ["' + SECRETISH.encode() + b'"]}',
        b'{"auths": "' + SECRETISH.encode() + b'"}',
        b"\xff\xfe" + SECRETISH.encode(),
    ],
    ids=[
        "empty",
        "not-json",
        "empty-list",
        "list",
        "no-auths",
        "auths-list",
        "auths-string",
        "not-utf8",
    ],
)
def test_an_unusable_file_is_refused_by_path_only(tmp_path: Path, content: bytes) -> None:
    directory = docker_config(tmp_path)
    source = login_file(tmp_path, content)
    with pytest.raises(RolloutError) as caught:
        registry_auth.install(directory, source)
    assert caught.value.step == "plan"
    assert caught.value.message == f"registry auth file {source} is not usable"
    assert caught.value.state is None
    shown = f"{caught.value} {caught.value.message!r} {caught.value.__context__!r}"
    assert SECRETISH not in shown, "the refusal carries file content"
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert os.listdir(directory) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_file_is_refused_by_path_only(tmp_path: Path) -> None:
    directory = docker_config(tmp_path)
    source = login_file(tmp_path, LOGIN)
    source.chmod(0)
    try:
        with pytest.raises(RolloutError) as caught:
            registry_auth.install(directory, source)
    finally:
        source.chmod(0o600)
    assert caught.value.message == f"registry auth file {source} is not usable"
    assert caught.value.__context__ is None
    assert os.listdir(directory) == []


def test_a_directory_in_place_of_the_file_is_refused(tmp_path: Path) -> None:
    directory = docker_config(tmp_path)
    source = tmp_path / "registry" / "config.json"
    source.mkdir(parents=True)
    with pytest.raises(RolloutError) as caught:
        registry_auth.install(directory, source)
    assert caught.value.message == f"registry auth file {source} is not usable"
    assert os.listdir(directory) == []


def test_the_suite_never_reads_the_hosts_auth_files(tmp_path: Path) -> None:
    """tests/conftest.py points the two host paths at a file that does not exist."""
    directory = docker_config(tmp_path)
    for source in (STAGING.registry_auth, PRODUCTION.registry_auth):
        assert registry_auth.install(directory, source) is False
    assert os.listdir(directory) == []
