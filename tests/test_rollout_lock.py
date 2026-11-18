"""The rollout lock: one rollout per target at a time, shared by manual and automatic runs."""

from __future__ import annotations

import errno
import fcntl
import os
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest
from scripts import staging_rollout as sr
from scripts.rollout_targets import PRODUCTION, STAGING
from scripts.staging_rollout import Completed, RolloutError

SHA = "a1b2c3d" + "0" * 33
STOP = "Stopped at lock: another rollout of staging is running; nothing was changed"

HOLDER = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_CREAT, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
print("held", flush=True)
sys.stdin.read()
"""


class CountingRunner:
    """A runner that records every call; the lock must stop a run before the first one."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        input: str | None = None,  # noqa: A002
    ) -> Completed:
        self.calls.append(list(argv))
        return Completed(1, "", "")


def lock_path(target: sr.Target = STAGING) -> Path:
    return sr.ROLLOUT_LOCK_DIR / f"{target.project}-rollout.lock"


@contextmanager
def held_by_another_process(path: Path) -> Iterator[None]:
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", HOLDER, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "held"
        yield
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        proc.wait(timeout=10)
        assert proc.stdout is not None
        proc.stdout.close()


def can_lock(path: Path) -> bool:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    finally:
        os.close(fd)
    return True


def test_lock_dir_defaults_to_the_host_lock_directory() -> None:
    # The autouse fixture points the directory elsewhere; the source default stays /run/lock.
    source = Path(sr.__file__).read_text()
    assert 'ROLLOUT_LOCK_DIR = Path("/run/lock")' in source


@pytest.mark.parametrize(
    "argv",
    [
        ["plan"],
        ["apply", "--sha", SHA],
        ["--target", "staging", "plan"],
        ["--target", "staging", "apply", "--sha", SHA],
    ],
)
def test_plan_and_apply_stop_at_lock_when_another_process_holds_it(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    runner = CountingRunner()
    with held_by_another_process(lock_path()):
        assert sr.main(argv, runner=runner) == 1
    assert runner.calls == []
    assert capsys.readouterr().out.splitlines() == [STOP]


def test_the_lock_is_released_after_a_run_and_after_a_stop(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sr, "apply", lambda *args, **kwargs: 0)
    assert (
        sr.main(["--target", "production", "apply", "--sha", SHA, "--host-digest", "0" * 12]) == 0
    )
    assert can_lock(lock_path(PRODUCTION))

    def stop(*args: object, **kwargs: object) -> sr.Plan:
        raise RolloutError("plan", "boom")

    monkeypatch.setattr(sr, "build_plan", stop)
    assert sr.main(["plan"], runner=CountingRunner()) == 1
    assert "Stopped at plan: boom" in capsys.readouterr().out
    assert can_lock(lock_path())

    def crash(*args: object, **kwargs: object) -> sr.Plan:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(sr, "build_plan", crash)
    with pytest.raises(RuntimeError):
        sr.main(["plan"], runner=CountingRunner())
    assert can_lock(lock_path())


def test_the_record_write_is_inside_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sr, "apply", lambda *args, **kwargs: 0)
    seen: list[bool] = []

    def record(runner: object, sha: str, out: object) -> None:
        seen.append(can_lock(lock_path()))

    monkeypatch.setattr(sr, "_record_rollout", record)
    assert sr.main(["apply", "--sha", SHA], runner=CountingRunner()) == 0
    assert seen == [False]
    assert can_lock(lock_path())


def test_an_existing_lock_file_is_opened_without_o_creat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = lock_path()
    opened: list[int] = []
    modes: list[int] = []
    real_open = os.open

    def recording_open(target: str | Path, flags: int, mode: int = 0o777, **kw: int) -> int:
        if Path(target) == path:
            opened.append(flags)
            if flags & os.O_CREAT:
                modes.append(mode)
        return real_open(target, flags, mode, **kw)

    monkeypatch.setattr(os, "open", recording_open)

    # Missing: created exclusively, then opened.
    with sr.hold_rollout_lock(STAGING):
        pass
    assert path.exists()
    plain = os.O_RDONLY | os.O_CLOEXEC
    created = os.O_RDONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    assert opened == [plain, created]
    assert modes == [0o644]

    # Existing: one plain open, never O_CREAT (fs.protected_regular).
    opened.clear()
    with sr.hold_rollout_lock(STAGING):
        pass
    assert opened == [plain]
    assert not any(flags & os.O_CREAT for flags in opened)


def test_a_lost_creation_race_retries_the_plain_open(monkeypatch: pytest.MonkeyPatch) -> None:
    path = lock_path()
    opened: list[int] = []
    real_open = os.open

    def racing_open(target: str | Path, flags: int, mode: int = 0o777, **kw: int) -> int:
        if Path(target) != path:
            return real_open(target, flags, mode, **kw)
        opened.append(flags)
        if flags & os.O_CREAT:
            # Another process creates the file between our two calls.
            fd = real_open(target, os.O_RDONLY | os.O_CREAT, 0o644)
            os.close(fd)
            raise FileExistsError(errno.EEXIST, "exists", str(target))
        return real_open(target, flags, mode, **kw)

    monkeypatch.setattr(os, "open", racing_open)
    with sr.hold_rollout_lock(STAGING):
        pass
    plain = os.O_RDONLY | os.O_CLOEXEC
    created = os.O_RDONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    assert opened == [plain, created, plain]


def test_a_new_lock_file_is_world_readable_whatever_the_umask() -> None:
    # open(O_CREAT, 0o644) is masked by the umask; under 077 the next user could not open it.
    path = lock_path()
    previous = os.umask(0o077)
    try:
        with sr.hold_rollout_lock(STAGING):
            pass
    finally:
        os.umask(previous)
    assert path.stat().st_mode & 0o777 == 0o644


def test_a_read_only_existing_lock_file_can_still_be_locked() -> None:
    path = lock_path()
    path.touch()
    path.chmod(0o444)
    with sr.hold_rollout_lock(STAGING):
        assert not can_lock(path)
    assert can_lock(path)


def test_a_held_lock_raises_the_lock_stop() -> None:
    with held_by_another_process(lock_path()):
        with pytest.raises(RolloutError) as caught:
            with sr.hold_rollout_lock(STAGING):
                pytest.fail("the lock was not held by this process")
    assert caught.value.step == "lock"
    assert caught.value.message == ("another rollout of staging is running; nothing was changed")


def test_an_unopenable_lock_directory_stops_without_runner_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "no-such-dir"
    monkeypatch.setattr(sr, "ROLLOUT_LOCK_DIR", missing)
    runner = CountingRunner()
    assert sr.main(["apply", "--sha", SHA], runner=runner) == 1
    assert runner.calls == []
    path = missing / "csa-staging-rollout.lock"
    assert capsys.readouterr().out.splitlines() == [
        f"Stopped at lock: cannot open the rollout lock {path}"
    ]


def test_an_unreadable_existing_lock_file_stops_with_the_cannot_open_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads any file")
    path = lock_path()
    path.touch()
    path.chmod(0)
    runner = CountingRunner()
    assert sr.main(["plan"], runner=runner) == 1
    assert runner.calls == []
    assert capsys.readouterr().out.splitlines() == [
        f"Stopped at lock: cannot open the rollout lock {path}"
    ]


def test_record_staged_takes_no_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_record(runner: object, target: object, sha: str, *, repo: object) -> int:
        seen.append(sha)
        return 0

    monkeypatch.setattr(sr, "record_staged_command", fake_record)
    with held_by_another_process(lock_path()):
        assert sr.main(["record-staged", "--sha", SHA], runner=CountingRunner()) == 0
    assert seen == [SHA]


def test_staging_and_production_use_different_lock_files(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert lock_path(STAGING).name == "csa-staging-rollout.lock"
    assert lock_path(PRODUCTION).name == "csa-prod-rollout.lock"
    runner = CountingRunner()
    with held_by_another_process(lock_path(STAGING)):
        # Production is not blocked by a staging rollout.
        with sr.hold_rollout_lock(PRODUCTION):
            pass
        assert sr.main(["--target", "staging", "plan"], runner=runner) == 1
    assert capsys.readouterr().out.splitlines() == [STOP]
    with held_by_another_process(lock_path(PRODUCTION)):
        with sr.hold_rollout_lock(STAGING):
            pass
        assert sr.main(["--target", "production", "plan"], runner=runner) == 1
    assert capsys.readouterr().out.splitlines() == [
        "Stopped at lock: another rollout of production is running; nothing was changed"
    ]
    assert runner.calls == []
