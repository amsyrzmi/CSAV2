"""Staging reset script: the read-only ``plan`` and its refusals, driven by a fake runner."""

from __future__ import annotations

import json
import stat
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from scripts import staging_reset as rs
from scripts.migrate import discover_migrations
from scripts.staging_rollout import SOCKET, Completed, RolloutError
from scripts.staging_roster import load_roster

from tests.test_staging_rollout import (
    FLAGS_TEXT,
    SHA,
    FakeRunner,
    Predicate,
    fail,
    has,
    ok,
    running_json,
)

GIB = 1 << 30
EXAMPLE_ROSTER = rs.REPO_ROOT / "deploy" / "staging-accounts.example.json"
SERVICES = ("gateway", "worker", "delivery", "frontend")
IMAGE_IDS = {service: "sha256:" + str(index) * 64 for index, service in enumerate(SERVICES)}
DB_BYTES = 52_428_800
VOLUME_FILES = {
    "csa-staging_staging-uploads": (12, 4096),
    "csa-staging_staging-hr-uploads": (3, 512),
}
NEWEST = discover_migrations()[-1][0]
SOCKET_PREFIX = f"docker --host {SOCKET}"
PROBE_ALL = "t|t|t"
PROBE_NONE = "f|f|f"


class ResetFake(FakeRunner):
    """``FakeRunner`` that also records ``run_tty`` and ``run_to_file``, in call order."""

    def __init__(self, rules: list[tuple[Predicate, Any]]) -> None:
        super().__init__(rules)
        self.tty_calls: list[list[str]] = []
        self.file_calls: list[list[str]] = []

    def run_tty(self, argv: Sequence[str], *, env: Mapping[str, str] | None) -> int:
        self.tty_calls.append(list(argv))
        return 0

    def run_to_file(self, argv: Sequence[str], *, env: Mapping[str, str] | None, path: Path) -> int:
        self.file_calls.append(list(argv))
        return 0

    @property
    def every_argv(self) -> list[list[str]]:
        return [*self.argvs, *self.tty_calls, *self.file_calls]


def _service_of(argv: Sequence[str]) -> str:
    return argv[-1].split("-")[2]  # csa-staging-<service>-1


def reset_runner(
    *,
    dirty: bool = False,
    off_main: bool = False,
    revisions: Mapping[str, str | None] | None = None,
    unreadable: Sequence[str] = (),
    database: str = "schema",  # schema, none or empty
    volumes_present: bool = True,
    secret_files: Mapping[str, Path] | None = None,
) -> ResetFake:
    revision = {service: SHA for service in SERVICES} | dict(revisions or {})

    def container(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        service = _service_of(argv)
        return fail() if service in unreadable else ok(IMAGE_IDS[service] + "\n")

    def image(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        service = next(name for name, image_id in IMAGE_IDS.items() if image_id == argv[-1])
        app = "frontend" if service == "frontend" else "python"
        ref = f"ghcr.io/amsyrzmi/csa-{app}@sha256:{service[0] * 64}"
        return ok(
            running_json(ref, "docker.io/other@sha256:" + "d" * 64, revision=revision[service])
        )

    def psql(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        sql = argv[-1]
        if argv[argv.index("-d") + 1] == "postgres":
            return ok("" if database == "missing" else f"{DB_BYTES}\n")
        if "to_regclass" in sql:
            return ok((PROBE_NONE if database == "empty" else PROBE_ALL) + "\n")
        if "SELECT slug" in sql:
            return ok("kedai-mawar\nteam-jo\n")
        if "count(*)" in sql:
            return ok("7\n")
        if "max(version)" in sql:
            return ok("0520\n")
        pytest.fail(f"unexpected sql: {sql}")

    def volume(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        name = next(arg for arg in argv if arg.endswith(":/data:ro")).split(":")[0]
        files, size = VOLUME_FILES[name]
        return ok(f"{files}\n{size}\t/data\n")

    rules: list[tuple[Predicate, Any]] = [
        (has("rev-parse HEAD"), ok(SHA + "\n")),
        (has("status --porcelain"), ok(" M scripts/x.py\n" if dirty else "")),
        (has("merge-base --is-ancestor"), fail(1) if off_main else ok()),
        (has("volume inspect"), ok() if volumes_present else fail()),
        (has("image inspect"), image),
        (has("inspect --format {{.Image}}"), container),
        (has("exec -T staging-db psql"), psql),
        (has(" run --rm --network none"), volume),
        (
            has("--profile migration-approved config --format json"),
            ok(
                json.dumps(
                    {"secrets": {n: {"file": str(p)} for n, p in (secret_files or {}).items()}}
                )
            ),
        ),
    ]
    return ResetFake(rules)


@pytest.fixture
def reset_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo with the flags file, the example roster as the roster, and plenty of disk."""
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    (repo / "deploy" / "staging-rollout.env").write_text(FLAGS_TEXT)
    roster = tmp_path / "roster.json"
    roster.write_text(EXAMPLE_ROSTER.read_text())
    monkeypatch.setattr(rs, "REPO_ROOT", repo)
    monkeypatch.setattr(rs, "ROSTER_PATH", roster)
    monkeypatch.setattr(rs, "_home_free", lambda: 50 * GIB)
    return repo


def _prepare(
    runner: ResetFake,
    repo: Path,
    tmp_path: Path,
    *,
    roster_path: Path | None = None,
    free: Callable[[], int] = lambda: 50 * GIB,
) -> rs.ResetContext:
    return rs.prepare(
        runner,
        repo,
        docker_config=tmp_path / "dc",
        roster_path=roster_path or rs.ROSTER_PATH,
        free_bytes=free,
    )


def test_targets_are_constants() -> None:
    assert rs.DATABASE == "csa_staging"
    assert rs.UPLOAD_VOLUMES == ("csa-staging_staging-uploads", "csa-staging_staging-hr-uploads")
    assert rs.CONFIRM_PHRASE == "wipe csa-staging"
    assert rs.DROP_DATABASE_SQL == "DROP DATABASE IF EXISTS csa_staging WITH (FORCE)"
    assert rs.CREATE_DATABASE_SQL == "CREATE DATABASE csa_staging OWNER csa"
    assert Path.home() / "csa-staging-backups" == rs.BACKUP_ROOT


FORBIDDEN = (
    "DROP",
    "CREATE DATABASE",
    " stop",
    " up ",
    "-delete",
    "pg_dump",
    "tar ",
    " rm ",
    " exec -i",
    "ALTER",
    "INSERT",
    "UPDATE",
    "TRUNCATE",
)


def _assert_read_only(runner: ResetFake) -> None:
    assert runner.tty_calls == []
    assert runner.file_calls == []
    for argv in runner.argvs:
        text = " ".join(argv)
        assert not any(token in text for token in FORBIDDEN), text
        if argv[0] == "git":
            continue
        assert argv[:3] == ["docker", "--host", SOCKET], text
        rest = argv[3:]
        if rest[0] == "run":
            # The one-off container that counts files: no network, nothing writable, :ro volume.
            assert rest[1:4] == ["--rm", "--network", "none"], text
            assert "--read-only" in rest and "--cap-drop" in rest, text
            mounts = [arg for arg in rest if ":/data" in arg]
            assert len(mounts) == 1 and mounts[0].endswith(":/data:ro"), text
            assert rest[-1] == "find /data -type f | wc -l; du -sb /data", text
        elif rest[0] == "compose":
            assert (
                "--project-name" in rest and rest[rest.index("--project-name") + 1] == "csa-staging"
            )
            if "exec" in rest:
                assert rest[-1].startswith("SELECT"), text
                assert rest[rest.index("exec") :][:4] == ["exec", "-T", "staging-db", "psql"], text
            else:
                assert "config" in rest and "--format" in rest, text
        else:
            assert rest[0] in {"inspect", "image", "volume"}, text
            assert "inspect" in rest[:2], text


def test_plan_runs_only_read_commands(reset_env: Path, tmp_path: Path) -> None:
    runner = reset_runner()
    context = _prepare(runner, reset_env, tmp_path)
    rs.format_plan(context)
    _assert_read_only(runner)
    assert any(argv[3] == "run" for argv in runner.argvs if argv[0] == "docker")
    assert any("exec" in argv for argv in runner.argvs)


def test_plan_shows_what_will_go(reset_env: Path, tmp_path: Path) -> None:
    context = _prepare(reset_runner(), reset_env, tmp_path)
    assert context.sha == SHA
    assert context.state == rs.StagingState(
        True,
        True,
        ("kedai-mawar", "team-jo"),
        7,
        DB_BYTES,
        {name: files for name, (files, _) in VOLUME_FILES.items()},
        {name: size for name, (_, size) in VOLUME_FILES.items()},
        "0520",
    )
    text = rs.format_plan(context)
    assert SHA[:7] in text
    assert "all four services run its image" in text
    assert f"python (gateway, worker, delivery): {context.python_image}" in text
    assert f"frontend: ghcr.io/amsyrzmi/csa-frontend@sha256:{'f' * 64}" in text
    assert f"now 0520; the rebuild will create it up to {NEWEST}" in text
    assert "Companies (2): kedai-mawar, team-jo" in text
    assert "Accounts: 7" in text
    assert "csa-staging_staging-uploads: 12 files, 4.0 KiB" in text
    assert "csa-staging_staging-hr-uploads: 3 files, 512 B" in text
    assert "platform admins: 3" in text
    assert "team-jo (Team Jo): packages customer_support, " in text
    assert "mode auto; 1 read_only, 1 tenant_admin, 3 tenant_staff" in text
    assert "syarikat-cekap" in text and "mode shadow; 1 tenant_admin" in text
    assert "to be redone by hand" in text
    for lost in rs.LOST:
        assert lost in text
    assert "50.0 GiB free; the backup takes about 50.0 MiB" in text
    assert rs.WIPE_COMMAND in text
    for person in (*load_roster(rs.ROSTER_PATH).platform_admins, *_members()):
        assert person.email not in text
        assert person.display_name not in text


def _members() -> list[Any]:
    return [m for company in load_roster(rs.ROSTER_PATH).companies for m in company.members]


def _bad_roster(path: Path) -> None:
    data = json.loads(EXAMPLE_ROSTER.read_text())
    data["platform_admins"][1]["email"] = data["platform_admins"][0]["email"].upper()
    path.write_text(json.dumps(data))


def _no_roster(path: Path) -> None:
    path.unlink()


@pytest.mark.parametrize(
    ("name", "runner_args", "roster", "free", "message"),
    [
        ("dirty checkout", {"dirty": True}, None, 50 * GIB, "tracked changes"),
        ("off origin/main", {"off_main": True}, None, 50 * GIB, "not on origin/main"),
        (
            "revision differs",
            {"revisions": {"worker": "f" * 40}},
            None,
            50 * GIB,
            "worker: runs fffffff",
        ),
        (
            "revision unreadable",
            {"revisions": {"frontend": None}},
            None,
            50 * GIB,
            "frontend: its image has no readable revision",
        ),
        (
            "image unreadable",
            {"unreadable": ("delivery",)},
            None,
            50 * GIB,
            "delivery: its image could not be read",
        ),
        ("missing roster", {}, _no_roster, 50 * GIB, "roster file not found"),
        ("invalid roster", {}, _bad_roster, 50 * GIB, "repeats the address"),
        (
            "missing secret",
            {"secret_files": {"staging_model_key": Path("/nonexistent/secret")}},
            None,
            50 * GIB,
            "missing secret files: staging_model_key",
        ),
        ("too little disk", {}, None, 100 * 1024 * 1024, "not enough free disk"),
    ],
    ids=[
        "dirty",
        "off-main",
        "revision-differs",
        "revision-unreadable",
        "image-unreadable",
        "missing-roster",
        "invalid-roster",
        "missing-secret",
        "no-disk",
    ],
)
def test_refusals(
    reset_env: Path,
    tmp_path: Path,
    name: str,
    runner_args: dict[str, Any],
    roster: Callable[[Path], None] | None,
    free: int,
    message: str,
) -> None:
    if roster:
        roster(rs.ROSTER_PATH)
    runner = reset_runner(**runner_args)
    with pytest.raises(RolloutError) as caught:
        _prepare(runner, reset_env, tmp_path, free=lambda: free)
    assert message in caught.value.message
    _assert_read_only(runner)
    if name == "invalid roster":
        data = json.loads(EXAMPLE_ROSTER.read_text())
        assert data["platform_admins"][0]["email"] not in caught.value.message.lower()


def test_a_refusal_before_the_state_is_read_touches_nothing_on_staging(
    reset_env: Path, tmp_path: Path
) -> None:
    runner = reset_runner(revisions={"worker": "f" * 40})
    with pytest.raises(RolloutError):
        _prepare(runner, reset_env, tmp_path)
    assert not any("psql" in argv or argv[3] == "run" for argv in runner.argvs if len(argv) > 3)


def test_plan_works_when_the_database_is_missing(
    reset_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = reset_runner(database="missing")
    assert rs.main(["plan"], runner) == 0
    out = capsys.readouterr().out
    assert "does not exist" in out and "nothing in it to back up" in out
    assert "Accounts" not in out
    _assert_read_only(runner)


def test_state_of_a_missing_database(reset_env: Path, tmp_path: Path) -> None:
    runner = reset_runner(database="missing")
    state = _prepare(runner, reset_env, tmp_path).state
    assert not state.database_exists
    assert not state.has_schema
    assert state.slugs == ()
    assert state.accounts == 0
    assert state.database_bytes == 0
    assert state.upload_files == {n: f for n, (f, _) in VOLUME_FILES.items()}
    queried = [a[a.index("-d") + 1] for a in runner.argvs if "psql" in a]
    assert queried == ["postgres"]


def test_plan_works_when_the_database_has_no_schema(
    reset_env: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = reset_runner(database="empty")
    state = _prepare(runner, reset_env, tmp_path).state
    assert state.database_exists
    assert not state.has_schema
    assert (state.slugs, state.accounts, state.schema_version) == ((), 0, None)
    assert state.database_bytes == DB_BYTES
    assert rs.main(["plan"], reset_runner(database="empty")) == 0
    out = capsys.readouterr().out
    assert "with no schema" in out
    assert f"it up to {NEWEST}" in out


def test_a_missing_volume_is_refused_not_created(reset_env: Path, tmp_path: Path) -> None:
    runner = reset_runner(volumes_present=False)
    with pytest.raises(RolloutError) as caught:
        _prepare(runner, reset_env, tmp_path)
    assert "csa-staging_staging-uploads does not exist" in caught.value.message
    assert not any(argv[3] == "run" for argv in runner.argvs if argv[0] == "docker")


def test_a_failing_psql_is_a_refusal(reset_env: Path, tmp_path: Path) -> None:
    runner = reset_runner()
    runner.rules.insert(0, (has("exec -T staging-db psql"), fail(2)))
    with pytest.raises(RolloutError) as caught:
        _prepare(runner, reset_env, tmp_path)
    assert caught.value.step == "state"


@pytest.mark.parametrize(
    "argv",
    [["wipe", "--project", "x"], ["wipe", "--database", "x"], ["plan", "--sha", "x"], []],
)
def test_cli_takes_no_target_arguments(argv: list[str]) -> None:
    runner = reset_runner()
    with pytest.raises(SystemExit) as caught:
        rs.main(argv, runner)
    assert caught.value.code == 2
    assert runner.every_argv == []


def test_targets_are_fixed_when_env_is_set(
    reset_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CSA_STAGING_PROJECT", "other")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "other")
    runner = reset_runner()
    assert rs.main(["plan"], runner) == 0
    capsys.readouterr()
    compose = [argv for argv in runner.argvs if "compose" in argv]
    assert compose
    for argv in compose:
        assert argv[argv.index("--project-name") + 1] == "csa-staging"
        assert argv.count("--project-name") == 1
    for _, env, _ in runner.calls:
        if env is not None:
            assert env["CSA_STAGING_PROJECT"] == "csa-staging"
            assert "COMPOSE_PROJECT_NAME" not in env
    mounted = sorted(
        arg.split(":")[0] for argv in runner.argvs for arg in argv if arg.endswith(":/data:ro")
    )
    assert mounted == sorted(rs.UPLOAD_VOLUMES)
    psql = [argv for argv in runner.argvs if "psql" in argv]
    assert {argv[argv.index("-U") + 1] for argv in psql} == {"csa"}
    assert {argv[argv.index("exec") + 2] for argv in psql} == {"staging-db"}


def test_volumes_are_read_through_the_gateways_python_image(
    reset_env: Path, tmp_path: Path
) -> None:
    runner = reset_runner()
    context = _prepare(runner, reset_env, tmp_path)
    assert context.python_image == "ghcr.io/amsyrzmi/csa-python@sha256:" + "g" * 64
    runs = [argv for argv in runner.argvs if argv[3:4] == ["run"]]
    assert len(runs) == 2
    assert all(context.python_image in argv for argv in runs)
    assert context.env["CSA_STAGING_PYTHON_IMAGE"] == context.python_image


def test_subprocess_runner_writes_a_new_private_file(tmp_path: Path) -> None:
    runner = rs.SubprocessResetRunner(cwd=tmp_path)
    target = tmp_path / "dump.bin"
    assert runner.run_to_file(["printf", "%s", "abc"], env=None, path=target) == 0
    assert target.read_bytes() == b"abc"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with pytest.raises(RolloutError) as caught:
        runner.run_to_file(["printf", "%s", "xyz"], env=None, path=target)
    assert caught.value.step == "backup"
    assert target.read_bytes() == b"abc"


def test_subprocess_runner_returns_the_exit_code_of_a_terminal_child(tmp_path: Path) -> None:
    runner = rs.SubprocessResetRunner(cwd=tmp_path)
    assert runner.run_tty(["sh", "-c", "exit 3"], env=None) == 3
    with pytest.raises(RolloutError):
        runner.run_tty(["definitely-not-a-command-here"], env=None)
