"""Staging rollout script: the read-only ``plan``, driven by a fake runner."""

from __future__ import annotations

import builtins
import io
import json
import os
import shutil
import stat
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from scripts import staging_rollout as sr
from scripts.staging_rollout import Completed, RolloutError

from tests.test_staging_compose import _compose_env, _make_synthetic_secrets

SHA = "a1b2c3d" + "0" * 33
OTHER_SHA = "a1b2c3d" + "f" * 33
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
PYTHON_REF = f"ghcr.io/amsyrzmi/csa-python@{DIGEST_A}"
FRONTEND_REF = f"ghcr.io/amsyrzmi/csa-frontend@{DIGEST_B}"
RUNNING_REF = "ghcr.io/amsyrzmi/csa-python@sha256:" + "c" * 64
SOCKET_PREFIX = f"docker --host {sr.SOCKET}"

ALL_FLAGS = {
    "CSA_STAGING_CONSOLE_ORIGIN": "https://staging-console.fourstacks.my",
    "CSA_STAGING_HOOKS_ORIGIN": "https://hooks.fourstacks.my",
    "CSA_STAGING_WORKSPACE_ASSISTANT": "true",
    "CSA_STAGING_GATEWAY_MODEL_SECRET": "staging_model_key",
    "CSA_STAGING_INVITE_MAIL": "true",
    "CSA_STAGING_EMAIL_ASSISTANT": "true",
}
FLAGS_TEXT = "".join(f"{key}={value}\n" for key, value in ALL_FLAGS.items())

Predicate = Callable[[Sequence[str]], bool]
Answer = Completed | Callable[[Sequence[str], Mapping[str, str] | None], Completed]


def has(text: str) -> Predicate:
    return lambda argv: text in " ".join(argv)


class FakeRunner:
    """Records every call and answers from ordered (predicate, answer) rules."""

    def __init__(self, rules: list[tuple[Predicate, Answer]]) -> None:
        self.rules = rules
        self.calls: list[tuple[list[str], dict[str, str] | None, str | None]] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        input: str | None = None,  # noqa: A002
    ) -> Completed:
        self.calls.append((list(argv), dict(env) if env is not None else None, input))
        for predicate, answer in self.rules:
            if predicate(argv):
                return answer(argv, env) if callable(answer) else answer
        pytest.fail(f"unexpected command: {' '.join(argv)}")

    @property
    def argvs(self) -> list[list[str]]:
        return [argv for argv, _, _ in self.calls]


def ok(stdout: str = "") -> Completed:
    return Completed(0, stdout, "")


def fail(code: int = 1, stderr: str = "") -> Completed:
    return Completed(code, "", stderr)


def versions_json(digest: str, *, tags: list[str]) -> str:
    return json.dumps(
        [
            {"name": "sha256:" + "9" * 64, "metadata": {"container": {"tags": ["old"]}}},
            {"name": digest, "metadata": {"container": {"tags": tags}}},
        ]
    )


def staging_runner(
    *,
    dirty: bool = False,
    off_main: bool = False,
    published: bool = True,
    run_status: str = "waiting",
    running: bool = True,
    applied: Sequence[str] = ("0484", "0485"),
    secret_files: Mapping[str, Path] | None = None,
) -> FakeRunner:
    py_tags = [SHA] if published else ["something-else"]
    fe_tags = [SHA] if published else ["something-else"]
    run_list = [
        {"databaseId": 1, "headSha": OTHER_SHA, "status": "completed", "conclusion": "success"},
        {"databaseId": 2, "headSha": SHA, "status": run_status, "conclusion": ""},
    ]
    rules: list[tuple[Predicate, Answer]] = [
        (has("rev-parse HEAD"), ok(SHA + "\n")),
        (has("status --porcelain"), ok(" M scripts/x.py\n" if dirty else "")),
        (has("merge-base --is-ancestor"), fail(1) if off_main else ok()),
        (has("csa-python/versions"), ok(versions_json(DIGEST_A, tags=py_tags))),
        (has("csa-frontend/versions"), ok(versions_json(DIGEST_B, tags=fe_tags))),
        (has("run list"), ok(json.dumps(run_list))),
        (
            has("image inspect"),
            ok(json.dumps([RUNNING_REF, "docker.io/other@sha256:" + "d" * 64])),
        ),
        (
            has("inspect --format {{.Image}}"),
            ok("sha256:" + "e" * 64 + "\n") if running else fail(),
        ),
        (has("exec -T staging-db psql"), ok("".join(f"{v}\n" for v in applied))),
        (
            has("--profile migration-approved config --format json"),
            ok(
                json.dumps(
                    {"secrets": {n: {"file": str(p)} for n, p in (secret_files or {}).items()}}
                )
            ),
        ),
    ]
    return FakeRunner(rules)


def make_secret_files(root: Path, *names: str) -> dict[str, Path]:
    root.mkdir(mode=0o700, exist_ok=True)
    paths = {}
    for name in names:
        paths[name] = root / name
        paths[name].write_text("SYNTHETIC-ONLY")
    return paths


@pytest.fixture
def rollout_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the module at tmp dirs and a fixed migration list; returns the secret dir."""
    secret_dir = tmp_path / "secrets"
    checks_dir = tmp_path / "checks"
    checks_dir.mkdir()
    (checks_dir / "0485.sql").write_text("SELECT 0;\n")
    monkeypatch.setattr(sr, "SECRET_DIR", secret_dir)
    monkeypatch.setattr(sr, "CHECKS_DIR", checks_dir)
    monkeypatch.setattr(
        sr,
        "discover_migrations",
        lambda: [(v, Path(f"{v}_x.sql")) for v in ("0484", "0485")],
    )
    return secret_dir


def test_load_flags_reads_the_six_keys(tmp_path: Path) -> None:
    path = tmp_path / "flags.env"
    path.write_text("# header\n\n" + FLAGS_TEXT)
    assert sr.load_flags(path) == ALL_FLAGS


def _bad_flags(case: str) -> str:
    lines = FLAGS_TEXT.splitlines()
    return {
        "UNKNOWN=1": FLAGS_TEXT + "UNKNOWN=1\n",
        "NO_EQUALS": FLAGS_TEXT + "NO_EQUALS\n",
        "CSA_STAGING_INVITE_MAIL=true (dup)": FLAGS_TEXT + "CSA_STAGING_INVITE_MAIL=true\n",
        "missing one key": "\n".join(lines[:-1]) + "\n",
    }[case]


@pytest.mark.parametrize(
    "bad", ["UNKNOWN=1", "NO_EQUALS", "CSA_STAGING_INVITE_MAIL=true (dup)", "missing one key"]
)
def test_load_flags_refuses_bad_files(tmp_path: Path, bad: str) -> None:
    path = tmp_path / "flags.env"
    path.write_text(_bad_flags(bad))
    with pytest.raises(RolloutError) as caught:
        sr.load_flags(path)
    assert caught.value.step == "flags"


def test_checked_in_flags_file_loads() -> None:
    flags = sr.load_flags(sr.REPO_ROOT / "deploy" / "staging-rollout.env")
    assert set(flags) == sr.FLAG_KEYS
    assert flags == ALL_FLAGS


def test_build_env_has_only_expected_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_TOKEN", "must-not-leak")
    monkeypatch.setenv("COMPOSE_PROFILES", "leaky")
    env = sr.build_env(
        ALL_FLAGS,
        {"python": PYTHON_REF, "frontend": FRONTEND_REF},
        docker_config=tmp_path / "dc",
        secret_dir=tmp_path / "secrets",
    )
    fixed = {
        "NO_LOCAL_ENV",
        "ENV",
        "COMPOSE_PROFILES",
        "CSA_STAGING_PROJECT",
        "CSA_STAGING_SECRET_DIR",
        "CSA_STAGING_PYTHON_IMAGE",
        "CSA_STAGING_FRONTEND_IMAGE",
    }
    assert set(env) == sr.FLAG_KEYS | fixed | {"PATH", "HOME", "DOCKER_CONFIG"}
    assert env["COMPOSE_PROFILES"] == ""
    assert env["NO_LOCAL_ENV"] == "1"
    assert env["ENV"] == "ci"
    assert env["CSA_STAGING_PROJECT"] == "csa-staging"
    assert env["CSA_STAGING_SECRET_DIR"] == str(tmp_path / "secrets")
    assert env["CSA_STAGING_PYTHON_IMAGE"] == PYTHON_REF
    assert env["CSA_STAGING_FRONTEND_IMAGE"] == FRONTEND_REF
    assert env["DOCKER_CONFIG"] == str(tmp_path / "dc")
    assert env["PATH"] == os.environ["PATH"]
    assert env["HOME"] == os.environ["HOME"]


def test_compose_base_is_exact() -> None:
    assert sr.compose_base() == [
        "docker",
        "--host",
        "unix:///var/run/docker.sock",
        "compose",
        "--env-file",
        "/dev/null",
        "--project-name",
        "csa-staging",
        "--file",
        "deploy/compose.staging-local-proposal.yml",
        "--file",
        "deploy/compose.staging-shared-caddy.yml",
        "--file",
        "deploy/compose.staging-pipeline.yml",
        "--profile",
        "staging-approved",
        "--profile",
        "pipeline-approved",
    ]


@pytest.mark.parametrize("case", ["dirty", "off_main"])
def test_target_refuses_dirty_or_off_main(case: str, tmp_path: Path) -> None:
    runner = staging_runner(dirty=case == "dirty", off_main=case == "off_main")
    with pytest.raises(RolloutError) as caught:
        sr.target_sha(runner, tmp_path)
    assert caught.value.step == "plan"
    assert not any("fetch" in argv for argv in runner.argvs)


def test_target_returns_clean_head_on_main(tmp_path: Path) -> None:
    runner = staging_runner()
    assert sr.target_sha(runner, tmp_path) == SHA
    assert not any("fetch" in argv for argv in runner.argvs)


def test_published_images_match_the_full_sha() -> None:
    wrong = "sha256:" + "1" * 64
    versions = json.dumps(
        [
            {"name": wrong, "metadata": {"container": {"tags": [SHA[:7], SHA[:12]]}}},
            {"name": DIGEST_A, "metadata": {"container": {"tags": ["x", SHA]}}},
        ]
    )
    runner = FakeRunner([(has("versions"), ok(versions))])
    images = sr.published_images(runner, SHA)
    assert images == {
        "python": f"ghcr.io/amsyrzmi/csa-python@{DIGEST_A}",
        "frontend": f"ghcr.io/amsyrzmi/csa-frontend@{DIGEST_A}",
    }
    assert all(env is None for _, env, _ in runner.calls)
    assert runner.argvs[0] == [
        "gh",
        "api",
        "/users/amsyrzmi/packages/container/csa-python/versions?per_page=100",
    ]


def test_published_images_none_when_an_app_is_missing() -> None:
    runner = FakeRunner(
        [
            (has("csa-python/versions"), ok(versions_json(DIGEST_A, tags=[SHA]))),
            (has("csa-frontend/versions"), ok(versions_json(DIGEST_B, tags=["other"]))),
        ]
    )
    assert sr.published_images(runner, SHA) is None


def test_published_images_refuse_a_malformed_digest() -> None:
    runner = FakeRunner([(has("versions"), ok(versions_json("sha256:xyz", tags=[SHA])))])
    with pytest.raises(RolloutError):
        sr.published_images(runner, SHA)


def test_gh_failure_carries_only_the_first_stderr_line() -> None:
    runner = FakeRunner([(has("versions"), fail(1, "HTTP 401: bad credentials\nsecond line\n"))])
    with pytest.raises(RolloutError) as caught:
        sr.published_images(runner, SHA)
    assert "HTTP 401: bad credentials" in str(caught.value)
    assert "second line" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "conclusion", "expected"),
    [
        ("waiting", "", "waiting for approval"),
        ("queued", "", "in progress"),
        ("in_progress", "", "in progress"),
        ("completed", "failure", "finished: failure"),
    ],
)
def test_publish_state(status: str, conclusion: str, expected: str) -> None:
    runs = [
        {"databaseId": 7, "headSha": OTHER_SHA, "status": "completed", "conclusion": "success"},
        {"databaseId": 5, "headSha": SHA, "status": "completed", "conclusion": "cancelled"},
        {"databaseId": 6, "headSha": SHA, "status": status, "conclusion": conclusion},
    ]
    runner = FakeRunner([(has("run list"), ok(json.dumps(runs)))])
    assert sr.publish_state(runner, SHA) == expected
    assert runner.argvs[0] == [
        "gh",
        "run",
        "list",
        "--workflow",
        "publish-staging-images.yml",
        "--limit",
        "20",
        "--json",
        "databaseId,headSha,status,conclusion",
    ]


def test_publish_state_without_a_run() -> None:
    runner = FakeRunner([(has("run list"), ok("[]"))])
    assert sr.publish_state(runner, SHA) == "no publish run found"


def test_pending_versions_is_checkout_minus_applied() -> None:
    assert sr.pending_versions(["0484", "0485", "0486"], ["0484"]) == ["0485", "0486"]
    assert sr.pending_versions(["0484"], ["0484"]) == []


def test_pending_versions_refuse_staging_ahead() -> None:
    with pytest.raises(RolloutError) as caught:
        sr.pending_versions(["0484"], ["0484", "0490", "0491"])
    assert caught.value.step == "plan"
    assert "0490" in str(caught.value)
    assert "0491" in str(caught.value)


def test_plan_reports_unpublished_head(
    capsys: pytest.CaptureFixture[str], rollout_env: Path
) -> None:
    runner = staging_runner(published=False)
    assert sr.main(["plan"], runner=runner) == 1
    out = capsys.readouterr().out
    assert "Stopped at plan:" in out
    assert "images for a1b2c3d are not published yet (waiting for approval)" in out
    assert not any(argv[0] == "docker" for argv in runner.argvs)


def test_plan_with_migrations_lists_steps_downtime_and_command(
    capsys: pytest.CaptureFixture[str], rollout_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sr,
        "discover_migrations",
        lambda: [(v, Path(f"{v}_x.sql")) for v in ("0485", "0486", "0487")],
    )
    paths = make_secret_files(rollout_env, "staging_owner_dsn")
    runner = staging_runner(applied=("0485",), secret_files=paths)
    assert sr.main(["plan"], runner=runner) == 0
    out = capsys.readouterr().out
    assert SHA in out
    assert PYTHON_REF in out
    assert FRONTEND_REF in out
    assert "Pending migrations: 0486, 0487" in out
    assert "stop gateway, frontend, worker, delivery" in out
    assert "apply migrations 0486, 0487" in out
    assert "confirm migrations recorded" in out
    assert "run checks: 0485.sql" in out
    assert (
        "Downtime: console unavailable and bot replies delayed "
        "(queued, not lost) until the services are back."
    ) in out
    assert out.rstrip("\n").endswith(
        ".venv/bin/python -m scripts.staging_rollout apply --migrations 0486,0487"
    )


def test_plan_image_only_has_no_migration_steps(
    capsys: pytest.CaptureFixture[str], rollout_env: Path
) -> None:
    paths = make_secret_files(rollout_env, "staging_owner_dsn")
    runner = staging_runner(secret_files=paths)
    assert sr.main(["plan"], runner=runner) == 0
    out = capsys.readouterr().out
    assert "Pending migrations: none" in out
    assert "stop gateway" not in out
    assert "apply migrations" not in out
    assert "Downtime" not in out
    assert "pull and verify images" in out
    assert "recreate gateway, worker, delivery, frontend" in out
    assert "check readiness" in out
    assert out.rstrip("\n").endswith(".venv/bin/python -m scripts.staging_rollout apply")


def test_plan_tolerates_stopped_containers(
    capsys: pytest.CaptureFixture[str], rollout_env: Path
) -> None:
    paths = make_secret_files(rollout_env, "staging_owner_dsn")
    runner = staging_runner(running=False, secret_files=paths)
    assert sr.main(["plan"], runner=runner) == 0
    out = capsys.readouterr().out
    assert out.count("unknown (not running)") == 4


def test_plan_shows_rollback_digests_when_running(
    capsys: pytest.CaptureFixture[str], rollout_env: Path
) -> None:
    paths = make_secret_files(rollout_env, "staging_owner_dsn")
    assert sr.main(["plan"], runner=staging_runner(secret_files=paths)) == 0
    out = capsys.readouterr().out
    assert out.count(RUNNING_REF) == 4
    assert "docker.io/other" not in out


def test_plan_lists_missing_secrets_by_name(
    capsys: pytest.CaptureFixture[str],
    rollout_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = make_secret_files(rollout_env, "present_one")
    paths["staging_owner_dsn"] = rollout_env / "migration" / "staging_owner_dsn"
    paths["smtp_password"] = rollout_env / "runtime" / "smtp_password"
    real_open = io.open
    secret_root = str(rollout_env)

    def guarded_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file).startswith(secret_root):
            raise AssertionError(f"secret file opened: {file}")
        return real_open(file, *args, **kwargs)

    # Path.open, read_text and read_bytes all go through io.open.
    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(builtins, "open", guarded_open)
    runner = staging_runner(secret_files=paths)
    assert sr.main(["plan"], runner=runner) == 2
    out = capsys.readouterr().out
    assert "Missing secret files: smtp_password, staging_owner_dsn" in out
    assert "apply will refuse until they exist" in out
    assert "present_one" not in out
    assert str(rollout_env) not in out


def test_plan_runs_no_mutating_command(rollout_env: Path) -> None:
    paths = make_secret_files(rollout_env, "staging_owner_dsn")
    runner = staging_runner(secret_files=paths)
    assert sr.main(["plan"], runner=runner) == 0
    base = sr.compose_base()
    docker_prefix = ["docker", "--host", sr.SOCKET]
    mutating = {"pull", "stop", "up", "rm", "run", "start", "restart", "down", "kill", "create"}
    assert runner.argvs
    for argv in runner.argvs:
        if argv[:2] == ["gh", "api"] or argv[:3] == ["gh", "run", "list"]:
            continue
        if argv[0] == "git":
            assert argv[argv.index("-C") + 2 :][0] in {"rev-parse", "status", "merge-base"}
            continue
        assert argv[0] == "docker", argv
        rest = argv[len(base) :] if argv[: len(base)] == base else argv[len(docker_prefix) :]
        assert not mutating & set(rest), argv
        assert "--rm" not in rest, argv


def test_secret_paths_from_real_compose_config(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable")
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir(mode=0o700)
    _make_synthetic_secrets(secret_dir)
    (secret_dir / "pipeline").mkdir(mode=0o700, exist_ok=True)
    for name in (
        "staging_model_key",
        "staging_telegram_bot_token",
        "staging_whatsapp_app_secret",
        "staging_whatsapp_verify_token",
    ):
        (secret_dir / "pipeline" / name).write_text("SYNTHETIC-ONLY")
    flags = sr.load_flags(sr.REPO_ROOT / "deploy" / "staging-rollout.env")
    env = sr.build_env(
        flags,
        {"python": PYTHON_REF, "frontend": FRONTEND_REF},
        docker_config=tmp_path,
        secret_dir=secret_dir,
    )
    env["HOME"] = _compose_env(secret_dir)["HOME"]
    paths = sr.secret_paths(sr.SubprocessRunner(cwd=sr.REPO_ROOT), env)
    assert "staging_owner_dsn" in paths
    assert "staging_model_key" in paths
    assert all(secret_dir in path.parents for path in paths.values())


def test_missing_secrets_returns_sorted_names_only(tmp_path: Path) -> None:
    present = tmp_path / "present"
    present.write_text("x")
    paths = {"b_secret": tmp_path / "nope", "present": present, "a_secret": tmp_path / "nada"}
    assert sr.missing_secrets(paths) == ["a_secret", "b_secret"]


def test_check_files_are_sorted_sql_only(tmp_path: Path) -> None:
    (tmp_path / "0486.sql").write_text("SELECT 0;")
    (tmp_path / "0485.sql").write_text("SELECT 0;")
    (tmp_path / "README.md").write_text("x")
    assert [p.name for p in sr.check_files(tmp_path)] == ["0485.sql", "0486.sql"]
    assert sr.check_files(tmp_path / "absent") == []


def test_apply_is_not_implemented_yet(capsys: pytest.CaptureFixture[str]) -> None:
    assert sr.main(["apply"], runner=FakeRunner([])) == 1
    assert "not implemented" in capsys.readouterr().err


def test_docker_config_dir_is_empty_private_and_removed(rollout_env: Path) -> None:
    paths = make_secret_files(rollout_env, "staging_owner_dsn")
    seen: list[tuple[int, list[str]]] = []
    dirs: list[Path] = []
    base = staging_runner(secret_files=paths)

    class Spy(FakeRunner):
        def run(
            self,
            argv: Sequence[str],
            *,
            env: Mapping[str, str] | None = None,
            input: str | None = None,  # noqa: A002
        ) -> Completed:
            if env is not None:
                directory = Path(env["DOCKER_CONFIG"])
                dirs.append(directory)
                seen.append((stat.S_IMODE(directory.stat().st_mode), os.listdir(directory)))
            return base.run(argv, env=env, input=input)

    assert sr.main(["plan"], runner=Spy([])) == 0
    assert seen
    assert all(mode == 0o700 and entries == [] for mode, entries in seen)
    assert len(set(dirs)) == 1
    assert not dirs[0].exists()
