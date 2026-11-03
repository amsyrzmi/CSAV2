"""Staging reset script: ``plan``, ``wipe`` and ``accounts``, driven by a fake runner."""

from __future__ import annotations

import inspect
import io
import json
import shlex
import stat
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from scripts import staging_reset as rs
from scripts.migrate import discover_migrations
from scripts.staging_rollout import SOCKET, Completed, RolloutError
from scripts.staging_roster import load_roster, parse_roster

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
        self.order: list[list[str]] = []  # every call to all three, as it happened
        self.envs: list[Mapping[str, str] | None] = []  # the env of every tty and file call
        self.files: list[Path] = []
        self.tty_answer: Callable[[list[str]], int] = lambda argv: 0
        self.file_answer: Callable[[list[str]], int] = lambda argv: 0

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        input: str | None = None,  # noqa: A002
    ) -> Completed:
        self.order.append(list(argv))
        return super().run(argv, env=env, input=input)

    def run_tty(self, argv: Sequence[str], *, env: Mapping[str, str] | None) -> int:
        self.tty_calls.append(list(argv))
        self.order.append(list(argv))
        self.envs.append(env)
        return self.tty_answer(list(argv))

    def run_to_file(self, argv: Sequence[str], *, env: Mapping[str, str] | None, path: Path) -> int:
        self.file_calls.append(list(argv))
        self.order.append(list(argv))
        self.envs.append(env)
        self.files.append(path)
        code = self.file_answer(list(argv))
        if code == 0:
            path.write_bytes(b"synthetic backup of " + path.name.encode())
        return code

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
    monkeypatch.setattr(rs, "BACKUP_ROOT", tmp_path / "backups")  # never the real home
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


# --- wipe ---------------------------------------------------------------------------------

BASELINE = "-- synthetic baseline\nCREATE TABLE synthetic (id int);\n"
PASSWORD = "synthetic-terminal-password"
T0 = datetime(2026, 11, 3, 8, 30, 15, tzinfo=UTC)
RECOVERY = (
    'Run wipe again, or restore from the backup (deploy/STAGING_PIPELINE.md, "Resetting staging").'
)
MOVED_HEAD = (
    "If HEAD has moved since this wipe started, restore the backup and roll out before "
    "wiping again."
)
PG_DUMP = "pg_dump -U csa -d csa_staging -Fc"
STOP = "stop gateway frontend worker delivery"
EMPTY_VOLUME = "find /data -mindepth 1 -delete"
EMPTINESS = "to_regclass('public.schema_migrations') IS NULL"
CAT_BASELINE = (
    "run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true "
    "--entrypoint cat"
)
LOAD_BASELINE = "-d csa_staging -v ON_ERROR_STOP=1 -q -f -"
MIGRATE = "--profile migration-approved run --rm --no-deps migrate"
READ_VERSION = "max(version)"
BOOTSTRAP = "--profile admin-bootstrap-approved run --rm --no-deps admin-bootstrap"
UP = "up --detach --wait"
READY = "exec csa-staging-gateway-1 python -c"
COMPANY_STEP = "exec -i csa-staging-gateway-1 python -m scripts.staging_accounts"


def wipe_runner(
    *,
    failing: str | None = None,
    tables_present: bool = False,
    version: str = NEWEST,
    **kwargs: Any,
) -> ResetFake:
    """A fake that answers every wipe step; ``failing`` names the one that goes wrong."""
    runner = reset_runner(**kwargs)

    def step(name: str, answer: Completed | None = None) -> Completed:
        return fail(3, f"synthetic reason for {name}\n") if failing == name else (answer or ok())

    def read_version(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        migrated = any(MIGRATE in " ".join(call) for call in runner.order)
        return ok((version if migrated else "0520") + "\n")

    def up(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        return fail(1) if failing == f"recreate {argv[-1]}" else ok()

    runner.rules[:0] = [
        (has(STOP), step("stop")),
        (has(rs.DROP_DATABASE_SQL), step("drop")),
        (has(rs.CREATE_DATABASE_SQL), step("create")),
        (has(EMPTY_VOLUME), step("empty volume")),
        (has(EMPTINESS), ok("f\n" if tables_present else "t\n")),
        (has("--entrypoint cat"), step("read baseline", ok(BASELINE))),
        (has(LOAD_BASELINE), step("baseline")),
        (has(MIGRATE), step("migrate", ok("applied\n"))),
        (has(READ_VERSION), read_version),
        (has(UP), up),
        (has("{{.State.Health.Status}}"), fail(1)),
        (has(READY), step("readiness")),
    ]
    if failing == "bootstrap":
        runner.tty_answer = lambda argv: 4
    return runner


def _wipe(
    runner: ResetFake,
    repo: Path,
    tmp_path: Path,
    *,
    typed: str = rs.CONFIRM_PHRASE,
    at: datetime = T0,
) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = rs.wipe(
        runner,
        repo,
        docker_config=tmp_path / "dc",
        read_line=lambda prompt: typed,
        is_terminal=lambda: True,
        free_bytes=lambda: 50 * GIB,
        clock=lambda: at,
        sleep=lambda seconds: None,
        out=lines.append,
    )
    return code, lines


def _ran(runner: ResetFake, fragment: str) -> bool:
    return any(fragment in " ".join(argv) for argv in runner.order)


def _assert_in_order(runner: ResetFake, steps: Sequence[tuple[str, ...]]) -> None:
    calls = [" ".join(argv) for argv in runner.order]
    position = -1
    for fragments in steps:
        later = [
            index
            for index, text in enumerate(calls)
            if index > position and all(fragment in text for fragment in fragments)
        ]
        assert later, f"{fragments} not found after call {position}"
        position = later[0]


def test_wipe_refuses_without_a_terminal(reset_env: Path, tmp_path: Path) -> None:
    runner = wipe_runner()
    lines: list[str] = []

    def no_prompt(prompt: str) -> str:
        raise AssertionError("must not prompt")

    code = rs.wipe(
        runner,
        reset_env,
        docker_config=tmp_path / "dc",
        read_line=no_prompt,
        is_terminal=lambda: False,
        free_bytes=lambda: 50 * GIB,
        clock=lambda: T0,
        sleep=lambda seconds: None,
        out=lines.append,
    )
    assert code != 0
    assert "terminal" in "\n".join(lines)
    assert runner.every_argv == []
    assert not rs.BACKUP_ROOT.exists()


@pytest.mark.parametrize("typed", ["", "yes", "WIPE CSA-STAGING", "wipe csa-staging "])
def test_wipe_refuses_a_wrong_phrase(reset_env: Path, tmp_path: Path, typed: str) -> None:
    runner = wipe_runner()
    code, lines = _wipe(runner, reset_env, tmp_path, typed=typed)
    assert code != 0
    assert "Nothing on staging was changed." in lines
    _assert_read_only(runner)
    assert not rs.BACKUP_ROOT.exists()


def test_wipe_treats_end_of_input_as_a_wrong_phrase(reset_env: Path, tmp_path: Path) -> None:
    runner = wipe_runner()

    def closed(prompt: str) -> str:
        raise EOFError

    code = rs.wipe(
        runner,
        reset_env,
        docker_config=tmp_path / "dc",
        read_line=closed,
        is_terminal=lambda: True,
        free_bytes=lambda: 50 * GIB,
        clock=lambda: T0,
        sleep=lambda seconds: None,
        out=lambda line: None,
    )
    assert code != 0
    _assert_read_only(runner)


def test_wipe_refuses_what_prepare_refuses_before_it_prompts(
    reset_env: Path, tmp_path: Path
) -> None:
    runner = wipe_runner(dirty=True)

    def no_prompt(prompt: str) -> str:
        raise AssertionError("must not prompt")

    lines: list[str] = []
    code = rs.wipe(
        runner,
        reset_env,
        docker_config=tmp_path / "dc",
        read_line=no_prompt,
        is_terminal=lambda: True,
        free_bytes=lambda: 50 * GIB,
        clock=lambda: T0,
        sleep=lambda seconds: None,
        out=lines.append,
    )
    assert code != 0
    assert "tracked changes" in "\n".join(lines)
    _assert_read_only(runner)


def test_wipe_runs_the_steps_in_order(reset_env: Path, tmp_path: Path) -> None:
    runner = wipe_runner()
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code == 0
    uploads, hr_uploads = rs.UPLOAD_VOLUMES
    first = load_roster(rs.ROSTER_PATH).platform_admins[0]
    _assert_in_order(
        runner,
        [
            (PG_DUMP,),
            ("--entrypoint tar", f"{uploads}:/data:ro"),
            ("--entrypoint tar", f"{hr_uploads}:/data:ro"),
            (STOP,),
            ("-d postgres", rs.DROP_DATABASE_SQL),
            ("-d postgres", rs.CREATE_DATABASE_SQL),
            ("--network none", f"{uploads}:/data ", EMPTY_VOLUME),
            ("--network none", f"{hr_uploads}:/data ", EMPTY_VOLUME),
            ("-d csa_staging", EMPTINESS),
            (CAT_BASELINE, "/app/db/schema_baseline.sql"),
            (LOAD_BASELINE,),
            (MIGRATE,),
            (READ_VERSION,),
            (BOOTSTRAP, first.email, "--create-first-platform-admin"),
            (UP, "gateway"),
            (UP, "worker"),
            (UP, "delivery"),
            (UP, "frontend"),
            (READY,),
        ],
    )
    # Backups go through run_to_file, the bootstrap's prompts through run_tty.
    assert [" ".join(argv) for argv in runner.file_calls][0].endswith(PG_DUMP)
    assert all("tar" in argv for argv in runner.file_calls[1:])
    assert len(runner.file_calls) == 3
    assert len(runner.tty_calls) == 1
    bootstrap_env = runner.envs[-1]
    assert bootstrap_env is not None
    assert bootstrap_env["CSA_STAGING_ADMIN_BOOTSTRAP_APPROVAL"] == "approved-isolated-staging"
    # The baseline text goes in on stdin, from the image; migrate carries its approval.
    loaded = [call for call in runner.calls if LOAD_BASELINE in " ".join(call[0])]
    assert [call[2] for call in loaded] == [BASELINE]
    migrated = [call for call in runner.calls if MIGRATE in " ".join(call[0])]
    assert migrated[0][1] is not None
    assert migrated[0][1]["CSA_STAGING_MIGRATION_APPROVAL"] == "approved-isolated-staging"
    assert "CSA_STAGING_MIGRATION_APPROVAL" not in (bootstrap_env or {})
    for argv in runner.order:
        if EMPTY_VOLUME in " ".join(argv) or "--entrypoint tar" in " ".join(argv):
            assert argv[3:6] == ["run", "--rm", "--network"] and argv[6] == "none"
            assert "--read-only" in argv and "ALL" in argv
    text = "\n".join(lines)
    assert "sign in" in text and first.email in text and "change the password" in text
    assert "scripts.staging_reset accounts" in text


def test_the_volume_is_emptied_not_removed(reset_env: Path, tmp_path: Path) -> None:
    runner = wipe_runner()
    assert _wipe(runner, reset_env, tmp_path)[0] == 0
    assert not any(
        "volume rm" in " ".join(argv) or " rm " in " ".join(argv) for argv in runner.order
    )
    emptied = [
        next(arg.split(":")[0] for arg in argv if arg.endswith(":/data"))
        for argv in runner.order
        if EMPTY_VOLUME in " ".join(argv)
    ]
    assert emptied == list(rs.UPLOAD_VOLUMES)


def test_backup_directory_and_files(reset_env: Path, tmp_path: Path) -> None:
    runner = wipe_runner()
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code == 0
    directory = rs.BACKUP_ROOT / "20261103T083015Z"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert {path.parent for path in runner.files} == {directory}
    assert sorted(path.name for path in directory.iterdir()) == sorted(
        [
            "csa_staging.dump",
            "manifest.json",
            *(f"{volume}.tar" for volume in rs.UPLOAD_VOLUMES),
        ]
    )
    assert str(directory) in "\n".join(lines)
    manifest = directory / "manifest.json"
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600
    assert json.loads(manifest.read_text()) == {
        "sha": SHA,
        "python_image": "ghcr.io/amsyrzmi/csa-python@sha256:" + "g" * 64,
        "frontend_image": "ghcr.io/amsyrzmi/csa-frontend@sha256:" + "f" * 64,
        "schema_version": "0520",
        "companies": 2,
        "accounts": 7,
        "upload_files": {name: files for name, (files, _) in VOLUME_FILES.items()},
        "dump_skipped": False,
    }
    text = manifest.read_text()
    for person in (*load_roster(rs.ROSTER_PATH).platform_admins, *_members()):
        assert person.email not in text
    # The success line carries the counts, so the owner sees what the backup holds.
    success = next(line for line in lines if line.startswith("Staging is rebuilt"))
    assert str(directory) in success
    assert "companies 2" in success and "accounts 7" in success and "upload files 15" in success


def test_the_manifest_is_written_last(reset_env: Path, tmp_path: Path) -> None:
    runner = wipe_runner()
    real = runner.run_to_file
    seen: list[bool] = []

    def watching(argv: Sequence[str], *, env: Mapping[str, str] | None, path: Path) -> int:
        seen.append((path.parent / "manifest.json").exists())
        return real(argv, env=env, path=path)

    runner.run_to_file = watching  # type: ignore[method-assign]
    assert _wipe(runner, reset_env, tmp_path)[0] == 0
    assert seen == [False, False, False]
    assert not list((rs.BACKUP_ROOT / "20261103T083015Z").glob("*.tmp"))


def test_backup_failure_changes_nothing(reset_env: Path, tmp_path: Path) -> None:
    runner = wipe_runner()
    runner.file_answer = lambda argv: 1 if "pg_dump" in argv else 0
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code != 0
    text = "\n".join(lines)
    assert "Stopped at backup" in text
    assert "Nothing on staging was changed." in lines
    assert RECOVERY not in lines
    assert not _ran(runner, " stop ")
    assert not _ran(runner, "DROP")
    assert not _ran(runner, EMPTY_VOLUME)


def test_a_volume_backup_failure_changes_nothing(reset_env: Path, tmp_path: Path) -> None:
    runner = wipe_runner()
    runner.file_answer = lambda argv: 2 if "tar" in argv else 0
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code != 0
    assert "Nothing on staging was changed." in lines
    assert not _ran(runner, " stop ")


def test_an_empty_backup_file_stops_the_wipe(reset_env: Path, tmp_path: Path) -> None:
    runner = wipe_runner()
    real = runner.run_to_file

    def write_nothing(argv: Sequence[str], *, env: Mapping[str, str] | None, path: Path) -> int:
        code = real(argv, env=env, path=path)
        path.write_bytes(b"")
        return code

    runner.run_to_file = write_nothing  # type: ignore[method-assign]
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code != 0
    assert "Stopped at backup" in "\n".join(lines)
    assert not _ran(runner, " stop ")


LATER_THAN = (
    ("stop", STOP),
    ("drop", rs.DROP_DATABASE_SQL),
    ("create", rs.CREATE_DATABASE_SQL),
    ("empty volume", EMPTY_VOLUME),
    ("baseline", LOAD_BASELINE),
    ("migrate", MIGRATE),
    ("bootstrap", BOOTSTRAP),
    ("recreate", UP),
    ("readiness", READY),
)


def _later_fragments(stage: str) -> list[str]:
    names = [name for name, _ in LATER_THAN]
    return [fragment for _, fragment in LATER_THAN[names.index(stage) + 1 :]]


@pytest.mark.parametrize(
    ("failing", "stage", "step", "state"),
    [
        ("stop", "stop", "stop", "may still be running"),
        ("drop", "drop", "drop", "services are stopped; csa_staging was not dropped"),
        ("create", "create", "create", "services are stopped; csa_staging does not exist"),
        ("empty volume", "empty volume", "empty volumes", "csa_staging has no schema"),
        ("read baseline", "empty volume", "baseline", "csa_staging has no schema"),
        ("baseline", "baseline", "baseline", "csa_staging has no schema"),
        ("migrate", "migrate", "migrate", "services are stopped"),
        ("version", "migrate", "version", "services are stopped"),
        ("bootstrap", "bootstrap", "bootstrap", "no platform admin"),
        ("recreate gateway", "recreate", "recreate gateway", "gateway did not start"),
        ("recreate frontend", "recreate", "recreate frontend", "frontend did not start"),
        ("readiness", "readiness", "readiness", "/readyz is failing"),
    ],
)
def test_each_failure_reports_its_state(
    reset_env: Path, tmp_path: Path, failing: str, stage: str, step: str, state: str
) -> None:
    runner = wipe_runner(failing=failing, version="0001" if failing == "version" else NEWEST)
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code != 0
    text = "\n".join(lines)
    assert f"Stopped at {step}" in text
    assert state in text
    assert str(rs.BACKUP_ROOT / "20261103T083015Z") in text
    assert RECOVERY in lines
    assert MOVED_HEAD in lines
    if failing == "version":
        assert "services are stopped" in text and NEWEST in text and "0001" in text
    if stage == "recreate":
        started = [argv[-1] for argv in runner.order if UP in " ".join(argv)]
        service = failing.split()[1]
        assert started == list(SERVICES[: SERVICES.index(service) + 1])
    if failing == "read baseline":
        assert not _ran(runner, LOAD_BASELINE)
    for fragment in _later_fragments(stage):
        if failing in {"version", "read baseline"} and fragment in {MIGRATE, LOAD_BASELINE}:
            continue
        assert not _ran(runner, fragment), fragment


def test_a_slow_start_is_waited_for(reset_env: Path, tmp_path: Path) -> None:
    runner = wipe_runner(failing="recreate gateway")
    runner.rules.insert(0, (has("{{.State.Health.Status}}"), ok("healthy\n")))
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code == 0, lines
    assert any("waiting for it to become healthy" in line for line in lines)


def test_an_interrupted_wipe_reports_its_state(reset_env: Path, tmp_path: Path) -> None:
    runner = wipe_runner()

    def interrupt(argv: list[str]) -> int:
        raise KeyboardInterrupt

    runner.tty_answer = interrupt
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code != 0
    assert "no platform admin" in "\n".join(lines)
    assert RECOVERY in lines
    assert not _ran(runner, UP)


def test_baseline_refuses_a_database_with_tables(reset_env: Path, tmp_path: Path) -> None:
    runner = wipe_runner(tables_present=True)
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code != 0
    assert "Stopped at baseline" in "\n".join(lines)
    assert "already has tables" in "\n".join(lines)
    assert not _ran(runner, "--entrypoint cat")
    assert not _ran(runner, LOAD_BASELINE)
    assert not _ran(runner, MIGRATE)


def _earlier_backup() -> tuple[Path, Path]:
    earlier = rs.BACKUP_ROOT / "20261102T010000Z"
    earlier.mkdir(parents=True, mode=0o700)
    dump = earlier / "csa_staging.dump"
    dump.write_bytes(b"the first, good backup")
    (earlier / "manifest.json").write_text(
        json.dumps(
            {
                "sha": SHA,
                "python_image": "python-image",
                "frontend_image": "frontend-image",
                "schema_version": "0500",
                "companies": 4,
                "accounts": 9,
                "upload_files": {name: 1 for name in VOLUME_FILES},
                "dump_skipped": False,
            }
        )
    )
    return earlier, dump


def test_wipe_after_a_partial_wipe(reset_env: Path, tmp_path: Path) -> None:
    earlier, dump = _earlier_backup()
    runner = wipe_runner(database="missing")
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code == 0, lines
    text = "\n".join(lines)
    assert "skipping pg_dump" in text
    assert not any("pg_dump" in argv for argv in runner.file_calls)
    assert [argv.count("tar") for argv in runner.file_calls] == [1, 1]
    assert _ran(runner, rs.DROP_DATABASE_SQL)
    assert _ran(runner, BOOTSTRAP)
    directory = rs.BACKUP_ROOT / "20261103T083015Z"
    assert sorted(p.name for p in directory.iterdir()) == sorted(
        ["manifest.json", *(f"{volume}.tar" for volume in rs.UPLOAD_VOLUMES)]
    )
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["dump_skipped"] is True
    assert manifest["companies"] == 0
    assert manifest["schema_version"] is None
    # The first, good backup is untouched and still named.
    assert dump.read_bytes() == b"the first, good backup"
    assert str(earlier) in text


def test_a_failure_after_a_partial_wipe_points_at_the_earlier_backup(
    reset_env: Path, tmp_path: Path
) -> None:
    earlier, _ = _earlier_backup()
    runner = wipe_runner(database="empty", failing="baseline")
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code != 0
    text = "\n".join(lines)
    assert str(rs.BACKUP_ROOT / "20261103T083015Z") in text
    assert str(earlier) in text
    assert f"most recent complete backup with real data is {earlier}" in text
    # A database with no schema is still dumped as it is.
    assert any("pg_dump" in argv for argv in runner.file_calls)


def test_second_wipe_keeps_the_first_backup(reset_env: Path, tmp_path: Path) -> None:
    assert _wipe(wipe_runner(), reset_env, tmp_path)[0] == 0
    first = rs.BACKUP_ROOT / "20261103T083015Z"
    contents = {path.name: path.read_bytes() for path in first.iterdir()}
    modified = {path.name: path.stat().st_mtime_ns for path in first.iterdir()}
    later = T0 + timedelta(minutes=5)
    assert _wipe(wipe_runner(), reset_env, tmp_path, at=later)[0] == 0
    second = rs.BACKUP_ROOT / "20261103T083515Z"
    assert sorted(path.name for path in rs.BACKUP_ROOT.iterdir()) == [first.name, second.name]
    assert {path.name: path.read_bytes() for path in first.iterdir()} == contents
    assert {path.name: path.stat().st_mtime_ns for path in first.iterdir()} == modified
    assert stat.S_IMODE(first.stat().st_mode) == 0o700
    assert stat.S_IMODE(second.stat().st_mode) == 0o700


def test_a_backup_directory_is_never_reused(reset_env: Path, tmp_path: Path) -> None:
    assert _wipe(wipe_runner(), reset_env, tmp_path)[0] == 0
    runner = wipe_runner()
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code != 0
    assert "Stopped at backup" in "\n".join(lines)
    assert runner.file_calls == []
    assert not _ran(runner, " stop ")


SYNTHETIC_DSN = "postgresql://csa:synthetic-dsn-secret@staging-db:5432/csa_staging"


@pytest.mark.parametrize(
    ("failing", "name"),
    [
        ("drop", "drop"),
        ("create", "create"),
        ("empty volume", "empty volume"),
        ("read baseline", "read baseline"),
        ("baseline", "baseline"),
    ],
)
def test_a_failure_after_the_drop_shows_the_reason(
    reset_env: Path, tmp_path: Path, failing: str, name: str
) -> None:
    code, lines = _wipe(wipe_runner(failing=failing), reset_env, tmp_path)
    assert code != 0
    assert f"synthetic reason for {name}" in "\n".join(lines)


def test_the_reason_is_the_tail_of_stderr(reset_env: Path, tmp_path: Path) -> None:
    runner = wipe_runner()
    noise = "".join(f"NOTICE: line {index}\n" for index in range(20))
    runner.rules.insert(0, (has(LOAD_BASELINE), fail(3, noise + "ERROR: synthetic failure\n")))
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code != 0
    text = "\n".join(lines)
    assert "ERROR: synthetic failure" in text
    assert "NOTICE: line 19" in text
    assert "NOTICE: line 0\n" not in text


def test_a_failed_read_after_the_drop_shows_the_reason(reset_env: Path, tmp_path: Path) -> None:
    runner = wipe_runner()
    runner.rules.insert(0, (has(EMPTINESS), fail(2, 'FATAL: database "csa_staging" is gone\n')))
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code != 0
    assert 'FATAL: database "csa_staging" is gone' in "\n".join(lines)
    assert not _ran(runner, LOAD_BASELINE)


def test_a_failed_migrate_shows_only_its_migration_error(reset_env: Path, tmp_path: Path) -> None:
    runner = wipe_runner()
    reason = "migration error: CSA_APP_PASSWORD is required to set the csa_app login password"
    stderr = (
        "Traceback (most recent call last):\n"
        f'  conninfo = "{SYNTHETIC_DSN}"\n'
        f"could not connect to {SYNTHETIC_DSN}\n"
        f"{reason}\n"
        f"  migration error: {SYNTHETIC_DSN}\n"
    )
    runner.rules.insert(0, (has(MIGRATE), Completed(1, "applied 0100_roles.sql\n", stderr)))
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code != 0
    text = "\n".join(lines)
    assert reason in text
    assert "applied 0100_roles.sql" in text
    assert "synthetic-dsn-secret" not in text
    assert "Traceback" not in text
    assert "not shown" not in text
    assert "Exception:" not in text
    assert not any(MIGRATE in line for line in lines)
    assert "Stopped at migrate" in text


def _failed_migrate(reset_env: Path, tmp_path: Path, stderr: str) -> tuple[ResetFake, list[str]]:
    runner = wipe_runner()
    runner.rules.insert(0, (has(MIGRATE), Completed(1, "applied 0100_roles.sql\n", stderr)))
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code != 0
    return runner, lines


def _assert_gives_the_manual_command(runner: ResetFake, lines: list[str]) -> None:
    """The printed line, as a shell would read it, is the command wipe ran, from the repo."""
    text = "\n".join(lines)
    assert "not shown" in text
    assert "last migration applied" in text
    assert "can be re-run safely" in text and "already applied are skipped" in text
    assert "wipe again" in text
    command = next(line for line in text.splitlines() if MIGRATE in line)
    words = shlex.split(command)
    assert words[:3] == ["cd", str(rs.REPO_ROOT), "&&"]
    words = words[3:]
    docker = words.index("docker")
    assignments = dict(word.split("=", 1) for word in words[:docker])
    ran_argv, ran_env, _ = next(call for call in runner.calls if MIGRATE in " ".join(call[0]))
    assert words[docker:] == ran_argv
    assert assignments["CSA_STAGING_MIGRATION_APPROVAL"] == "approved-isolated-staging"
    assert ran_env is not None
    assert assignments.items() <= ran_env.items()
    assert {"CSA_STAGING_PYTHON_IMAGE", "CSA_STAGING_SECRET_DIR"} <= assignments.keys()
    # Not worth repeating, and this run's docker config is a temp directory that won't outlive it.
    assert not {"PATH", "HOME", "DOCKER_CONFIG"} & assignments.keys()


def test_a_failed_migrate_names_the_exception_class_and_nothing_after_the_colon(
    reset_env: Path, tmp_path: Path
) -> None:
    last = (
        'psycopg.OperationalError: connection to server at "staging-db", '
        "password=synthetic-secret failed"
    )
    stderr = f"Traceback (most recent call last):\n  {SYNTHETIC_DSN}\n{last}\n\n"
    runner, lines = _failed_migrate(reset_env, tmp_path, stderr)
    text = "\n".join(lines)
    assert "Exception: psycopg.OperationalError" in text.splitlines()
    assert "synthetic-secret" not in text
    assert "synthetic-dsn-secret" not in text
    assert "connection to server" not in text
    assert "password=" not in text
    assert "Traceback" not in text
    _assert_gives_the_manual_command(runner, lines)


@pytest.mark.parametrize(
    "last",
    [
        "synthetic-secret: not an identifier",
        "two words.here: synthetic-secret",
        "postgresql://csa:synthetic-secret@staging-db/csa_staging",
        "NotDotted: synthetic-secret",
        "psycopg.1bad: synthetic-secret",
        "psycopg.Error",
        "LINE 1: SELECT synthetic-secret",
        "        ^",
    ],
)
def test_a_failed_migrate_with_no_valid_class_prints_nothing_from_the_line(
    reset_env: Path, tmp_path: Path, last: str
) -> None:
    runner, lines = _failed_migrate(reset_env, tmp_path, f"Traceback\npsycopg.Earlier: x\n{last}\n")
    text = "\n".join(lines)
    assert "Exception:" not in text
    assert "synthetic-secret" not in text
    assert "psycopg." not in text
    assert last.strip() not in text
    _assert_gives_the_manual_command(runner, lines)


def test_a_failed_migrate_with_no_stderr_still_gives_the_command(
    reset_env: Path, tmp_path: Path
) -> None:
    runner, lines = _failed_migrate(reset_env, tmp_path, "")
    assert not any("Exception:" in line for line in lines)
    _assert_gives_the_manual_command(runner, lines)


def _t1_partial(reset_env: Path, tmp_path: Path) -> tuple[Path, list[str]]:
    # T1: pg_dump succeeds, a tar fails. A partial directory is left, and nothing changed.
    runner = wipe_runner()
    runner.file_answer = lambda argv: 1 if "tar" in argv else 0
    code, lines = _wipe(runner, reset_env, tmp_path, at=T0)
    assert code != 0
    directory = rs.BACKUP_ROOT / "20261103T083015Z"
    assert (directory / "csa_staging.dump").exists()
    assert not (directory / "manifest.json").exists()
    return directory, lines


def _t2_good(reset_env: Path, tmp_path: Path) -> tuple[Path, list[str]]:
    # T2: a good backup of the real data, then the wipe fails at migrate.
    code, lines = _wipe(
        wipe_runner(failing="migrate"), reset_env, tmp_path, at=T0 + timedelta(minutes=5)
    )
    assert code != 0
    directory = rs.BACKUP_ROOT / "20261103T083515Z"
    assert (directory / "manifest.json").exists()
    return directory, lines


def test_a_partial_backup_says_it_is_incomplete(reset_env: Path, tmp_path: Path) -> None:
    t1, lines = _t1_partial(reset_env, tmp_path)
    assert f"This run's backup: {t1}: (incomplete, do not restore)" in lines
    assert "Nothing on staging was changed." in lines


def test_a_stop_at_the_backup_does_not_advise_a_restore(reset_env: Path, tmp_path: Path) -> None:
    earlier, _ = _earlier_backup()
    runner = wipe_runner()
    runner.file_answer = lambda argv: 1 if "tar" in argv else 0
    code, lines = _wipe(runner, reset_env, tmp_path)
    assert code != 0
    # Nothing changed, so staging itself still has its data; the list alone is enough.
    assert any(line.startswith(f"  {earlier}: revision") for line in lines)
    assert not any("most recent complete backup" in line for line in lines)
    assert not any("No complete backup" in line for line in lines)


def test_a_later_stop_tells_the_backups_apart(reset_env: Path, tmp_path: Path) -> None:
    t1, _ = _t1_partial(reset_env, tmp_path)
    t2, lines = _t2_good(reset_env, tmp_path)
    assert f"  {t1}: (incomplete, do not restore)" in lines
    this_run = next(line for line in lines if line.startswith(f"This run's backup: {t2}"))
    assert f"revision {SHA[:7]}" in this_run
    assert "schema 0520" in this_run
    assert "companies 2" in this_run and "accounts 7" in this_run
    assert "upload files 15" in this_run
    # This run's backup holds the real data, so nothing points away from it.
    assert not any("most recent complete backup" in line for line in lines)


def test_success_after_failures_names_the_backup_with_the_real_data(
    reset_env: Path, tmp_path: Path
) -> None:
    t1, _ = _t1_partial(reset_env, tmp_path)
    t2, _ = _t2_good(reset_env, tmp_path)
    # T3: staging is already nearly empty, so this backup holds nothing, and the wipe succeeds.
    code, lines = _wipe(
        wipe_runner(database="empty"), reset_env, tmp_path, at=T0 + timedelta(minutes=10)
    )
    assert code == 0, lines
    t3 = rs.BACKUP_ROOT / "20261103T084015Z"
    success = next(line for line in lines if line.startswith("Staging is rebuilt"))
    assert str(t3) in success
    assert "companies 0" in success
    advice = next(line for line in lines if "most recent complete backup" in line)
    assert str(t2) in advice
    assert str(t1) not in advice
    assert f"  {t1}: (incomplete, do not restore)" in lines
    assert any(line.startswith(f"  {t2}: revision") for line in lines)
    assert (t1 / "csa_staging.dump").exists()


def test_a_stop_after_failures_names_the_backup_with_the_real_data(
    reset_env: Path, tmp_path: Path
) -> None:
    _t1_partial(reset_env, tmp_path)
    t2, _ = _t2_good(reset_env, tmp_path)
    code, lines = _wipe(
        wipe_runner(database="missing", failing="baseline"),
        reset_env,
        tmp_path,
        at=T0 + timedelta(minutes=10),
    )
    assert code != 0
    text = "\n".join(lines)
    assert f"most recent complete backup with real data is {t2}" in text
    assert "no database dump" in text


def test_no_backup_with_real_data_is_said_plainly(reset_env: Path, tmp_path: Path) -> None:
    code, lines = _wipe(wipe_runner(database="empty"), reset_env, tmp_path)
    assert code == 0
    assert any("No complete backup here holds any companies." in line for line in lines)


@pytest.mark.parametrize("content", ["", "{not json", "[]", '{"sha": "only this"}'])
def test_an_unreadable_manifest_counts_as_incomplete(
    reset_env: Path, tmp_path: Path, content: str
) -> None:
    earlier, _ = _earlier_backup()
    (earlier / "manifest.json").write_text(content)
    _, lines = _wipe(wipe_runner(database="empty"), reset_env, tmp_path)
    assert f"  {earlier}: (incomplete, do not restore)" in lines
    assert not any(str(earlier) in line and "most recent" in line for line in lines)


# --- accounts -----------------------------------------------------------------------------


def accounts_runner(
    *,
    existing: Sequence[str] = (),
    company_code: int = 0,
    company_output: str = "team-jo: company: created\n",
    **kwargs: Any,
) -> ResetFake:
    """A fake staging after a wipe; ``existing`` lists the platform admins already there."""
    runner = reset_runner(**kwargs)

    def admin_read(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        return ok("1\n" if any(f"'{email}'" in argv[-1] for email in existing) else "0\n")

    runner.rules[:0] = [
        (has("FROM console_users WHERE email"), admin_read),
        (has(COMPANY_STEP), Completed(company_code, company_output, "")),
    ]
    return runner


def _accounts(
    runner: ResetFake,
    repo: Path,
    tmp_path: Path,
    *,
    password: str = PASSWORD,
) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = rs.accounts(
        runner,
        repo,
        docker_config=tmp_path / "dc",
        is_terminal=lambda: True,
        read_password=lambda prompt: password,
        out=lines.append,
    )
    return code, lines


def _company_calls(runner: ResetFake) -> list[tuple[list[str], Any, str | None]]:
    return [call for call in runner.calls if COMPANY_STEP in " ".join(call[0])]


def test_accounts_adds_admins_then_runs_the_company_step(reset_env: Path, tmp_path: Path) -> None:
    roster = load_roster(rs.ROSTER_PATH)
    first, *further = roster.platform_admins
    output = "team-jo: members[0] (tenant_admin) jo.admin@example.test: invited, mail sent\n"
    runner = accounts_runner(company_output=output)
    code, lines = _accounts(runner, reset_env, tmp_path)
    assert code == 0
    assert len(runner.tty_calls) == len(further) == 2
    for argv, admin in zip(runner.tty_calls, further, strict=True):
        text = " ".join(argv)
        assert "--profile admin-add-approved run --rm --no-deps admin-add" in text
        assert f"--approver-email {first.email}" in text
        assert f"--email {admin.email}" in text
        assert argv[argv.index("--display-name") + 1] == admin.display_name
        assert "--add-platform-admin" in text
    for env in runner.envs:
        assert env is not None
        assert env["CSA_STAGING_ADMIN_ADD_APPROVAL"] == "approved-isolated-staging"
    _assert_in_order(
        runner,
        [
            ("FROM console_users WHERE email", further[0].email),
            ("--email", further[0].email),
            ("FROM console_users WHERE email", further[1].email),
            ("--email", further[1].email),
            (COMPANY_STEP,),
        ],
    )
    calls = _company_calls(runner)
    assert len(calls) == 1
    argv, env, stdin = calls[0]
    assert argv[:3] == ["docker", "--host", SOCKET]
    assert " ".join(argv[3:]) == COMPANY_STEP
    assert stdin is not None
    line, _, rest = stdin.partition("\n")
    assert line == PASSWORD
    assert parse_roster(json.loads(rest)) == roster
    # The password is in no argv, no env and no message.
    for argv, env, _ in runner.calls:
        assert PASSWORD not in " ".join(argv)
        assert PASSWORD not in (env or {}).values()
    for argv in runner.every_argv:
        assert PASSWORD not in " ".join(argv)
    for env in runner.envs:
        assert PASSWORD not in (env or {}).values()
    assert not any(PASSWORD in line for line in lines)
    # The child's stdout reaches the owner.
    assert output.rstrip() in lines


@pytest.mark.parametrize("code", [1, 2, 125])
def test_accounts_returns_the_company_steps_exit_code(
    reset_env: Path, tmp_path: Path, code: int
) -> None:
    runner = accounts_runner(company_code=code, company_output="line from the child\n")
    got, lines = _accounts(runner, reset_env, tmp_path)
    assert got == code
    assert "line from the child" in lines
    text = "\n".join(lines)
    assert ("Renew invitation on the Team page" in text) is (code == 2)
    assert PASSWORD not in text


def test_accounts_does_not_show_the_company_steps_stderr(reset_env: Path, tmp_path: Path) -> None:
    runner = accounts_runner()
    runner.rules.insert(0, (has(COMPANY_STEP), Completed(1, "", f"traceback with {PASSWORD}")))
    code, lines = _accounts(runner, reset_env, tmp_path)
    assert code == 1
    assert not any(PASSWORD in line for line in lines)
    assert any("exited 1" in line for line in lines)


def test_accounts_stops_when_admin_add_fails(reset_env: Path, tmp_path: Path) -> None:
    runner = accounts_runner()
    runner.tty_answer = lambda argv: 1
    code, lines = _accounts(runner, reset_env, tmp_path)
    assert code != 0
    text = "\n".join(lines)
    assert "must have signed in and changed their password" in text
    assert PASSWORD not in text
    assert len(runner.tty_calls) == 1
    assert _company_calls(runner) == []


def test_accounts_skips_an_admin_who_already_exists(reset_env: Path, tmp_path: Path) -> None:
    roster = load_roster(rs.ROSTER_PATH)
    second, third = roster.platform_admins[1:]
    runner = accounts_runner(existing=[second.email])
    code, lines = _accounts(runner, reset_env, tmp_path)
    assert code == 0
    assert [argv[argv.index("--email") + 1] for argv in runner.tty_calls] == [third.email]
    assert any(second.email in line and "already there" in line for line in lines)
    reads = [argv for argv in runner.argvs if "FROM console_users WHERE email" in " ".join(argv)]
    assert len(reads) == 2
    assert all("is_platform_admin" in argv[-1] for argv in reads)
    # Every admin there: a re-run prompts for nobody and still runs the company step.
    runner = accounts_runner(existing=[second.email, third.email])
    assert _accounts(runner, reset_env, tmp_path)[0] == 0
    assert runner.tty_calls == []
    assert len(_company_calls(runner)) == 1


def test_an_address_is_quoted_in_the_admin_read(reset_env: Path, tmp_path: Path) -> None:
    data = json.loads(EXAMPLE_ROSTER.read_text())
    data["platform_admins"][1]["email"] = "o'brien@example.test"
    rs.ROSTER_PATH.write_text(json.dumps(data))
    runner = accounts_runner()
    assert _accounts(runner, reset_env, tmp_path)[0] == 0
    assert any("email = 'o''brien@example.test'" in argv[-1] for argv in runner.argvs)


def test_accounts_refuses_without_a_terminal(reset_env: Path, tmp_path: Path) -> None:
    runner = accounts_runner()

    def no_prompt(prompt: str) -> str:
        raise AssertionError("must not prompt")

    lines: list[str] = []
    code = rs.accounts(
        runner,
        reset_env,
        docker_config=tmp_path / "dc",
        is_terminal=lambda: False,
        read_password=no_prompt,
        out=lines.append,
    )
    assert code != 0
    assert "terminal" in "\n".join(lines)
    assert runner.every_argv == []


def test_accounts_output_needs_a_terminal_as_well_as_its_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stream(io.StringIO):
        def __init__(self, tty: bool) -> None:
            super().__init__()
            self.tty = tty

        def isatty(self) -> bool:
            return self.tty

    monkeypatch.setattr("sys.stdin", Stream(True))
    monkeypatch.setattr("sys.stdout", Stream(False))  # a pipe would carry addresses elsewhere
    assert not rs._on_a_terminal()
    monkeypatch.setattr("sys.stdout", Stream(True))
    assert rs._on_a_terminal()
    monkeypatch.setattr("sys.stdin", Stream(False))
    assert not rs._on_a_terminal()


def test_accounts_refuses_a_staging_with_no_schema(reset_env: Path, tmp_path: Path) -> None:
    runner = accounts_runner(database="empty")

    def no_prompt(prompt: str) -> str:
        raise AssertionError("must not prompt")

    lines: list[str] = []
    code = rs.accounts(
        runner,
        reset_env,
        docker_config=tmp_path / "dc",
        is_terminal=lambda: True,
        read_password=no_prompt,
        out=lines.append,
    )
    assert code != 0
    assert "run wipe first" in "\n".join(lines)
    assert runner.tty_calls == []
    assert _company_calls(runner) == []


def test_accounts_refuses_a_staging_that_was_not_wiped(reset_env: Path, tmp_path: Path) -> None:
    runner = accounts_runner()
    runner.rules.insert(0, (has("SELECT slug"), ok("demo-company\nteam-jo\n")))

    def no_prompt(prompt: str) -> str:
        raise AssertionError("must not ask for the password")

    lines: list[str] = []
    code = rs.accounts(
        runner,
        reset_env,
        docker_config=tmp_path / "dc",
        is_terminal=lambda: True,
        read_password=no_prompt,
        out=lines.append,
    )
    assert code != 0
    text = "\n".join(lines)
    assert "demo-company" in text and "not wiped" in text
    assert "team-jo" not in text
    assert runner.tty_calls == []
    assert _company_calls(runner) == []
    assert not _ran(runner, "FROM console_users WHERE email")


def test_accounts_accepts_a_staging_with_only_roster_companies(
    reset_env: Path, tmp_path: Path
) -> None:
    runner = accounts_runner()  # the fake's two companies are both in the example roster
    assert _accounts(runner, reset_env, tmp_path)[0] == 0
    assert len(_company_calls(runner)) == 1


def test_accounts_refuses_what_prepare_refuses(reset_env: Path, tmp_path: Path) -> None:
    runner = accounts_runner(off_main=True)
    code, lines = _accounts(runner, reset_env, tmp_path)
    assert code != 0
    assert "not on origin/main" in "\n".join(lines)
    assert runner.tty_calls == []
    assert _company_calls(runner) == []


@pytest.mark.parametrize("password", ["", "broken-\udcff-bytes"])
def test_accounts_refuses_a_password_it_cannot_send(
    reset_env: Path, tmp_path: Path, password: str
) -> None:
    runner = accounts_runner()
    code, lines = _accounts(runner, reset_env, tmp_path, password=password)
    assert code != 0
    assert runner.tty_calls == []
    assert _company_calls(runner) == []
    assert any("password" in line for line in lines)
    assert not any(password and password in line for line in lines)


def test_the_real_runner_cannot_echo_its_input(tmp_path: Path) -> None:
    runner = rs.SubprocessResetRunner(cwd=tmp_path)
    with pytest.raises(RolloutError) as caught:
        runner.run(["definitely-not-a-command-here"], input=PASSWORD)
    assert PASSWORD not in str(caught.value)
    assert PASSWORD not in caught.value.message
    not_executable = tmp_path / "not-executable"
    not_executable.write_text("")
    with pytest.raises(RolloutError) as caught:
        runner.run([str(not_executable)], input=PASSWORD)
    assert PASSWORD not in caught.value.message
    # A child that exits at once, without reading its stdin, costs no error at all.
    finished = runner.run(["sh", "-c", "exit 3"], input=PASSWORD * 100_000)
    assert finished.returncode == 3
    assert PASSWORD not in repr(finished)


def test_company_step_input_round_trips(reset_env: Path) -> None:
    roster = load_roster(rs.ROSTER_PATH)
    assert any(company.deployment_mode is None for company in roster.companies)
    line, _, rest = rs.company_step_input(PASSWORD, roster).partition("\n")
    assert line == PASSWORD
    assert parse_roster(json.loads(rest)) == roster


# --- main ---------------------------------------------------------------------------------


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize("command", ["wipe", "accounts"])
def test_main_refuses_without_a_terminal(
    reset_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO())
    runner = reset_runner()
    assert rs.main([command], runner) == 1
    assert "terminal" in capsys.readouterr().out
    assert runner.every_argv == []


def test_main_refuses_a_wipe_with_its_output_piped(
    reset_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Compose allocates a TTY only when stdout is a terminal; without one, getpass echoes.
    monkeypatch.setattr("sys.stdin", _Terminal())
    monkeypatch.setattr("sys.stdout", io.StringIO())

    def no_prompt(prompt: str = "") -> str:
        raise AssertionError("must not prompt")

    monkeypatch.setattr("builtins.input", no_prompt)
    runner = wipe_runner()
    assert rs.main(["wipe"], runner) == 1
    assert "terminal" in rs.sys.stdout.getvalue()  # type: ignore[attr-defined]
    assert runner.every_argv == []
    assert not rs.BACKUP_ROOT.exists()


def test_wipe_and_accounts_both_default_to_the_full_terminal_check() -> None:
    for command in (rs.wipe, rs.accounts):
        assert inspect.signature(command).parameters["is_terminal"].default is rs._on_a_terminal


def test_main_runs_wipe(
    reset_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(rs, "_on_a_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": rs.CONFIRM_PHRASE)
    runner = wipe_runner()
    assert rs.main(["wipe"], runner) == 0
    assert _ran(runner, rs.DROP_DATABASE_SQL)
    assert "scripts.staging_reset accounts" in capsys.readouterr().out
    assert len(list(rs.BACKUP_ROOT.iterdir())) == 1


def test_main_runs_accounts(
    reset_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(rs, "_on_a_terminal", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": PASSWORD)
    runner = accounts_runner(company_code=2, company_output="mail not sent\n")
    assert rs.main(["accounts"], runner) == 2
    out = capsys.readouterr().out
    assert "mail not sent" in out
    assert PASSWORD not in out
    assert _company_calls(runner)[0][2].startswith(PASSWORD + "\n")  # type: ignore[union-attr]
