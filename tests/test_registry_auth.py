"""The optional read-only registry login of the rollout scripts (``scripts.registry_auth``).

Every value here is synthetic. The file content is a credential: no assertion message may carry
it, so the checks below compare it with ``not in`` and never print it.
"""

from __future__ import annotations

import os
import shutil
import signal
import stat
from collections.abc import Iterator
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
        b'{"auths": {}}',
        b'{"auths": {"ghcr.io": {}}, "credsStore": "' + SECRETISH.encode() + b'"}',
        b'{"auths": {"ghcr.io": {"auth": "' + SECRETISH.encode() + b'"}}, "credHelpers": {}}',
        b'{"auths": {"ghcr.io": {"auth": "' + SECRETISH.encode() + b'"}}, "credsHelper": "x"}',
        b'{"auths": {"ghcr.io": {"auth": "' + SECRETISH.encode() + b'"}}, "proxies": {}}',
        b'{"auths": {"ghcr.io": {"auth": "' + SECRETISH.encode() + b'"}}, "currentContext": "x"}',
        b'{"auths": {"ghcr.io": {"auth": "'
        + SECRETISH.encode()
        + b'"}}, "cliPluginsExtraDirs": []}',
        b'{"auths": {"ghcr.io": {}}}',
        b'{"auths": {"ghcr.io": {"identitytoken": "' + SECRETISH.encode() + b'"}}}',
        b'{"auths": {"ghcr.io": {"auth": ""}}}',
        b'{"auths": {"ghcr.io": {"auth": ["' + SECRETISH.encode() + b'"]}}}',
        b'{"auths": {"ghcr.io": "' + SECRETISH.encode() + b'"}}',
        b'{"auths": {"ghcr.io": {"auth": "' + SECRETISH.encode() + b'"}, "other.io": {}}}',
        # Under MAX_BYTES, so json.loads really runs and raises RecursionError.
        b"[" * 60_000,
        b'{"a":' * 12_000,
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
        "auths-empty",
        "auths-and-credsStore",
        "auths-and-credHelpers",
        "auths-and-credsHelper",
        "auths-and-proxies",
        "auths-and-currentContext",
        "auths-and-cliPluginsExtraDirs",
        "entry-without-auth",
        "entry-with-identitytoken-only",
        "entry-auth-empty",
        "entry-auth-not-string",
        "entry-not-object",
        "one-entry-without-auth",
        "deeply-nested-list",
        "deeply-nested-object",
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


def assert_refused_by_path_only(directory: Path, source: Path) -> None:
    with pytest.raises(RolloutError) as caught:
        registry_auth.install(directory, source)
    assert caught.value.step == "plan"
    assert caught.value.message == f"registry auth file {source} is not usable"
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert os.listdir(directory) == []


def test_a_login_with_two_inline_entries_is_copied(tmp_path: Path) -> None:
    directory = docker_config(tmp_path)
    content = b'{"auths": {"ghcr.io": {"auth": "YQ=="}, "other.io": {"auth": "Yg=="}}}'
    assert registry_auth.install(directory, login_file(tmp_path, content)) is True
    assert (directory / "config.json").read_bytes() == content


def test_a_symlink_to_a_valid_login_is_refused(tmp_path: Path) -> None:
    directory = docker_config(tmp_path)
    target = tmp_path / "elsewhere.json"
    target.write_bytes(LOGIN)
    source = tmp_path / "registry" / "config.json"
    source.parent.mkdir()
    source.symlink_to(target)
    assert_refused_by_path_only(directory, source)


def test_a_dangling_symlink_is_refused_not_read_as_absent(tmp_path: Path) -> None:
    directory = docker_config(tmp_path)
    source = tmp_path / "registry" / "config.json"
    source.parent.mkdir()
    source.symlink_to(tmp_path / "nowhere.json")
    assert_refused_by_path_only(directory, source)


def test_a_symlink_to_a_large_file_is_refused(tmp_path: Path) -> None:
    directory = docker_config(tmp_path)
    target = tmp_path / "large.bin"
    target.write_bytes(b"\0" * (2 * registry_auth.MAX_BYTES))
    source = tmp_path / "registry" / "config.json"
    source.parent.mkdir()
    source.symlink_to(target)
    assert_refused_by_path_only(directory, source)


@pytest.fixture
def alarm() -> Iterator[None]:
    """Fail, not hang, when a regression blocks on a FIFO with no writer."""

    def expired(signum: int, frame: object) -> None:
        raise TimeoutError("install blocked on the FIFO")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(5)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.usefixtures("alarm")
def test_a_fifo_is_refused_without_blocking(tmp_path: Path) -> None:
    directory = docker_config(tmp_path)
    source = tmp_path / "registry" / "config.json"
    source.parent.mkdir()
    os.mkfifo(source, 0o600)
    assert_refused_by_path_only(directory, source)


def test_a_file_over_64_kib_is_refused(tmp_path: Path) -> None:
    directory = docker_config(tmp_path)
    content = LOGIN.rstrip(b"\n") + b" " * (registry_auth.MAX_BYTES + 1 - len(LOGIN) + 1)
    assert len(content) == registry_auth.MAX_BYTES + 1
    assert_refused_by_path_only(directory, login_file(tmp_path, content))


def test_a_file_of_exactly_64_kib_is_copied(tmp_path: Path) -> None:
    assert registry_auth.MAX_BYTES == 64 * 1024
    directory = docker_config(tmp_path)
    content = LOGIN.rstrip(b"\n") + b" " * (registry_auth.MAX_BYTES - len(LOGIN) + 1)
    assert len(content) == registry_auth.MAX_BYTES
    assert registry_auth.install(directory, login_file(tmp_path, content)) is True
    assert (directory / "config.json").read_bytes() == content


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
def test_a_failed_copy_stops_by_path_only(tmp_path: Path) -> None:
    directory = docker_config(tmp_path)
    source = login_file(tmp_path, LOGIN)
    directory.chmod(0o500)
    try:
        with pytest.raises(RolloutError) as caught:
            registry_auth.install(directory, source)
    finally:
        directory.chmod(0o700)
    assert caught.value.step == "plan"
    assert caught.value.message == f"could not copy the registry auth file {source}"
    shown = f"{caught.value} {caught.value.message!r}"
    assert SECRETISH not in shown, "the refusal carries file content"
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert os.listdir(directory) == []
