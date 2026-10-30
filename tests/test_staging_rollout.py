"""Staging rollout script: ``plan`` and ``apply``, driven by a fake runner."""

from __future__ import annotations

import builtins
import io
import json
import os
import re
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
PREV_SHA = "0f9e8d7" + "1" * 33  # the revision the running images were built from
PLANNED_SHA = "9f8e7d6" + "2" * 33  # a SHA the owner planned before HEAD moved
RUNNING_FORMAT = '{"digests": {{json .RepoDigests}}, "labels": {{json .Config.Labels}} }'
REVISION = "org.opencontainers.image.revision"
RESTART = (
    "docker --host unix:///var/run/docker.sock start csa-staging-gateway-1 "
    "csa-staging-frontend-1 csa-staging-worker-1 csa-staging-delivery-1"
)
RERUN = "Re-run plan, then apply, once the cause is fixed."
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


def running_json(*refs: str, revision: str | None = PREV_SHA) -> str:
    """What the running-image inspect prints: the digests and the image labels."""
    labels = {REVISION: revision} if revision else None
    return json.dumps({"digests": list(refs), "labels": labels})


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
            ok(running_json(RUNNING_REF, "docker.io/other@sha256:" + "d" * 64)),
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
        ("pending", "", "in progress"),
        ("requested", "", "in progress"),
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
    assert out.rstrip("\n").splitlines()[-1] == (
        f".venv/bin/python -m scripts.staging_rollout apply --sha {SHA} --migrations 0486,0487"
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
    assert out.rstrip("\n").splitlines()[-1] == (
        f".venv/bin/python -m scripts.staging_rollout apply --sha {SHA}"
    )


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
    assert "Rollback references (current containers):" in out
    assert "running now" not in out


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


# --- apply -------------------------------------------------------------------------------

IMAGE_REFS = {"python": PYTHON_REF, "frontend": FRONTEND_REF}


def step_label(argv: Sequence[str]) -> str | None:
    """Name the calls that matter for ordering; reads of the plan phase are None."""
    base = sr.compose_base()
    docker = ["docker", "--host", sr.SOCKET]
    if list(argv[: len(base)]) == base:
        rest = list(argv[len(base) :])
        if rest[:2] == ["--profile", "migration-approved"]:
            rest = rest[2:]
        if rest[:2] == ["config", "--quiet"]:
            return "config"
        if rest[:1] == ["stop"]:
            return "stop"
        if rest[:1] == ["run"]:
            return "migrate"
        if rest[:1] == ["up"]:
            return f"up {rest[-1]}"
        if rest[:1] == ["ps"]:
            return "ps"
        if rest[:1] == ["exec"]:
            return "check" if "-f" in rest else "select"
        return None
    if list(argv[:3]) == docker and argv[3:4] == ["pull"]:
        return "pull python" if "csa-python" in argv[4] else "pull frontend"
    if list(argv[:3]) == docker and argv[3:4] == ["exec"]:
        return "ready"
    return None


def labels(runner: FakeRunner) -> list[str]:
    return [label for argv in runner.argvs if (label := step_label(argv))]


class ApplyRig:
    """A repo dir, a fake staging host, and a way to run ``apply`` against it."""

    def __init__(
        self,
        tmp_path: Path,
        secret_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        applied: Sequence[str] = ("0484", "0485"),
        checkout: Sequence[str] = ("0484", "0485"),
        overrides: Sequence[tuple[Predicate, Answer]] = (),
        secrets: Sequence[str] = ("staging_owner_dsn",),
        recorded: Sequence[str] | None = None,
        running: bool = True,
    ) -> None:
        self.repo = tmp_path / "repo"
        (self.repo / "deploy").mkdir(parents=True)
        (self.repo / "deploy" / "staging-rollout.env").write_text(FLAGS_TEXT)
        self.secret_dir = secret_dir
        self.docker_config = tmp_path / "dc"
        self.sleeps: list[float] = []
        self.lines: list[str] = []
        self.marks: list[tuple[str, int]] = []  # each line with the number of calls made so far
        monkeypatch.setattr(
            sr, "discover_migrations", lambda: [(v, Path(f"{v}_x.sql")) for v in checkout]
        )
        migrated = {"done": False}

        def select(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
            versions = (recorded or checkout) if migrated["done"] else applied
            return ok("".join(f"{v}\n" for v in versions))

        def migrate(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
            migrated["done"] = True
            return ok("applying migrations\n")

        def inspect_pulled(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
            ref = argv[-1]
            return ok(
                json.dumps(
                    [
                        {
                            "RepoDigests": [ref],
                            "Config": {"Labels": {"org.opencontainers.image.revision": SHA}},
                        }
                    ]
                )
            )

        def is_pulled_inspect(argv: Sequence[str]) -> bool:
            return list(argv[3:5]) == ["image", "inspect"] and argv[-1] in IMAGE_REFS.values()

        paths = make_secret_files(secret_dir, *[n for n in secrets if not n.startswith("absent")])
        for name in secrets:
            paths.setdefault(name, secret_dir / "absent" / name)
        self.runner = staging_runner(applied=applied, secret_files=paths, running=running)
        apply_rules: list[tuple[Predicate, Answer]] = [
            *overrides,
            (is_pulled_inspect, inspect_pulled),
            (lambda argv: step_label(argv) == "config", ok()),
            (lambda argv: (step_label(argv) or "").startswith("pull"), ok()),
            (lambda argv: step_label(argv) == "stop", ok()),
            (lambda argv: step_label(argv) == "migrate", migrate),
            (lambda argv: (step_label(argv) or "").startswith("up "), ok()),
            (lambda argv: step_label(argv) == "ready", ok()),
            (lambda argv: step_label(argv) == "check", ok("0\n")),
            (lambda argv: step_label(argv) == "ps", ok("gateway Up 5 seconds\nworker Up\n")),
            (lambda argv: step_label(argv) == "select", select),
        ]
        self.runner.rules[:0] = apply_rules

    def out(self, line: str) -> None:
        self.lines.append(line)
        self.marks.append((line, len(self.runner.calls)))

    def apply(self, confirmed: Sequence[str] = (), sha: str = SHA) -> int:
        return sr.apply(
            self.runner,
            self.repo,
            list(confirmed),
            sha=sha,
            docker_config=self.docker_config,
            secret_dir=self.secret_dir,
            checks_dir=sr.CHECKS_DIR,
            sleep=self.sleeps.append,
            out=self.out,
        )

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def rig_factory(
    tmp_path: Path, rollout_env: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., ApplyRig]:
    def make(**kwargs: Any) -> ApplyRig:
        return ApplyRig(tmp_path, rollout_env, monkeypatch, **kwargs)

    return make


MIGRATING = {"applied": ("0484", "0485"), "checkout": ("0484", "0485", "0486", "0487")}
UP_ALL = ["up gateway", "up worker", "up delivery", "up frontend"]


def test_parse_migrations() -> None:
    assert sr.parse_migrations("0486,0487") == ["0486", "0487"]
    assert sr.parse_migrations("0486, 0487,") == ["0486", "0487"]
    assert sr.parse_migrations(None) == []
    assert sr.parse_migrations("") == []


def test_apply_image_only_runs_steps_in_order(rig_factory: Callable[..., ApplyRig]) -> None:
    rig = rig_factory()
    assert rig.apply() == 0
    assert labels(rig.runner) == [
        "select",
        "config",
        "pull python",
        "pull frontend",
        *UP_ALL,
        "ready",
        "check",
        "ps",
    ]
    assert "gateway Up 5 seconds" in rig.text
    assert rig.lines[-1] == "Rollout of a1b2c3d finished."


def test_apply_pulls_by_digest_and_recreates_with_the_documented_argv(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory()
    assert rig.apply() == 0
    docker = ["docker", "--host", sr.SOCKET]
    assert [*docker, "pull", PYTHON_REF] in rig.runner.argvs
    assert [*docker, "image", "inspect", FRONTEND_REF] in rig.runner.argvs
    assert [*sr.compose_base(), "config", "--quiet"] in rig.runner.argvs
    assert [
        *sr.compose_base(),
        "up",
        "--detach",
        "--wait",
        "--wait-timeout",
        "300",
        "--no-deps",
        "--pull",
        "never",
        "worker",
    ] in rig.runner.argvs
    assert [
        *docker,
        "exec",
        "csa-staging-gateway-1",
        "python",
        "-c",
        "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=5)",
    ] in rig.runner.argvs
    assert [*sr.compose_base(), "ps", "--format", "{{.Service}} {{.Status}}"] in rig.runner.argvs


def test_apply_with_migrations_stops_all_four_then_migrates_then_verifies(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(**MIGRATING)
    assert rig.apply(["0486", "0487"]) == 0
    assert labels(rig.runner) == [
        "select",
        "config",
        "pull python",
        "pull frontend",
        "stop",
        "migrate",
        "select",
        *UP_ALL,
        "ready",
        "check",
        "ps",
    ]
    stop = next(a for a in rig.runner.argvs if step_label(a) == "stop")
    assert stop[len(sr.compose_base()) :] == ["stop", "gateway", "frontend", "worker", "delivery"]
    migrate = next(a for a in rig.runner.argvs if step_label(a) == "migrate")
    assert migrate == [
        *sr.compose_base(),
        "--profile",
        "migration-approved",
        "run",
        "--rm",
        "--no-deps",
        "migrate",
    ]
    assert "migrate exit=0" in rig.lines
    assert "applying migrations" in rig.text


def test_migration_approval_is_only_in_the_migrate_env(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(**MIGRATING)
    assert rig.apply(["0486", "0487"]) == 0
    key = "CSA_STAGING_MIGRATION_APPROVAL"
    with_approval = [(argv, env) for argv, env, _ in rig.runner.calls if env and key in env]
    assert len(with_approval) == 1
    argv, env = with_approval[0]
    assert step_label(argv) == "migrate"
    assert env[key] == "approved-isolated-staging"


@pytest.mark.parametrize("confirmed", [[], ["0486"], ["0486", "0488"]])
def test_apply_refuses_when_pending_changed_since_plan(
    rig_factory: Callable[..., ApplyRig], confirmed: list[str]
) -> None:
    rig = rig_factory(**MIGRATING)
    assert rig.apply(confirmed) == 1
    assert labels(rig.runner) == ["select"]
    assert "Stopped at confirm: pending migrations changed since plan; run plan again" in rig.text


def test_apply_refuses_migrations_flag_when_nothing_pending(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory()
    assert rig.apply(["0486"]) == 1
    assert labels(rig.runner) == ["select"]
    assert "Stopped at confirm" in rig.text


def test_apply_refuses_missing_secrets_before_any_change(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(secrets=("staging_owner_dsn", "absent_one"))
    assert rig.apply() == 1
    assert labels(rig.runner) == ["select"]
    assert "Stopped at preflight: missing secret files: absent_one" in rig.text


def test_plan_reads_secrets_before_staging(rig_factory: Callable[..., ApplyRig]) -> None:
    rig = rig_factory()
    rig.apply()
    joined = [" ".join(argv) for argv in rig.runner.argvs]
    secrets = next(i for i, j in enumerate(joined) if "config --format json" in j)
    first_read = next(
        i for i, j in enumerate(joined) if "inspect --format {{.Image}}" in j or "psql" in j
    )
    assert secrets < first_read


@pytest.mark.parametrize("bad", ["digest", "revision"])
def test_pull_verification_rejects_mismatch(rig_factory: Callable[..., ApplyRig], bad: str) -> None:
    label = "org.opencontainers.image.revision"
    entry = {
        "RepoDigests": [FRONTEND_REF if bad == "digest" else PYTHON_REF],
        "Config": {"Labels": {label: OTHER_SHA if bad == "revision" else SHA}},
    }
    rig = rig_factory(
        overrides=[
            (
                lambda argv: list(argv[3:5]) == ["image", "inspect"] and argv[-1] == PYTHON_REF,
                ok(json.dumps([entry])),
            )
        ]
    )
    assert rig.apply() == 1
    seen = labels(rig.runner)
    assert not any(label.startswith("up") or label == "stop" for label in seen)
    assert "Stopped at pull:" in rig.text


def test_migrate_failure_stops_without_retry_or_recreate(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(
        **MIGRATING,
        overrides=[(lambda argv: step_label(argv) == "migrate", Completed(1, "boom\n", ""))],
    )
    assert rig.apply(["0486", "0487"]) == 1
    seen = labels(rig.runner)
    assert seen.count("migrate") == 1
    assert seen[seen.index("migrate") + 1 :] == ["select"]  # the re-read decides the recovery
    assert "migrate exit=1" in rig.lines
    assert "Stopped at migrate:" in rig.text
    assert "gateway, frontend, worker and delivery are stopped" in rig.text
    assert rig.lines[rig.lines.index("To bring the previous version back:") + 1] == RESTART
    assert "Rollback images" not in rig.text
    assert "Migrations were applied" not in rig.text
    assert rig.lines[-1] == RERUN


def test_migrate_must_record_every_pending_version(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(**MIGRATING, recorded=("0484", "0485", "0486"))
    assert rig.apply(["0486", "0487"]) == 1
    assert "schema_migrations lacks 0487" in rig.text
    assert not any(label.startswith("up") for label in labels(rig.runner))


def test_failed_check_stops_all_four_and_exits_nonzero(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(overrides=[(lambda argv: step_label(argv) == "check", ok("3\n"))])
    assert rig.apply() == 1
    last = next(a for a in reversed(rig.runner.argvs) if step_label(a))
    assert step_label(last) == "stop"
    assert last[len(sr.compose_base()) :] == ["stop", "gateway", "frontend", "worker", "delivery"]
    assert "Stopped at check 0485.sql: printed 3, expected 0" in rig.text
    assert "services stopped; investigate before the console is used again" in rig.text


def test_failed_check_with_failed_stop_does_not_claim_services_stopped(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(
        overrides=[
            (lambda argv: step_label(argv) == "check", ok("3\n")),
            (lambda argv: step_label(argv) == "stop", fail(1)),
        ]
    )
    assert rig.apply() == 1
    assert "Stopped at check 0485.sql: printed 3, expected 0" in rig.text
    assert "compose stop failed (exit 1)" in rig.text
    assert "may still be running; stop them by hand" in rig.text
    assert "services stopped;" not in rig.text


def test_every_check_file_runs_on_each_apply(
    rig_factory: Callable[..., ApplyRig], rollout_env: Path
) -> None:
    (sr.CHECKS_DIR / "0486.sql").write_text("SELECT 1 - 1;\n")
    rig = rig_factory()
    assert rig.apply() == 0
    assert labels(rig.runner).count("check") == 2
    inputs = [text for argv, _, text in rig.runner.calls if step_label(argv) == "check"]
    assert inputs == ["SELECT 0;\n", "SELECT 1 - 1;\n"]
    check = next(a for a in rig.runner.argvs if step_label(a) == "check")
    assert check[len(sr.compose_base()) :] == [
        "exec",
        "-T",
        "staging-db",
        "psql",
        "-U",
        "csa",
        "-d",
        "csa_staging",
        "-tA",
        "-v",
        "ON_ERROR_STOP=1",
        "-f",
        "-",
    ]


def test_readiness_retries_then_fails(rig_factory: Callable[..., ApplyRig]) -> None:
    rig = rig_factory(overrides=[(lambda argv: step_label(argv) == "ready", fail(1))])
    assert rig.apply() == 1
    assert labels(rig.runner).count("ready") == 5
    assert rig.sleeps == [3, 3, 3, 3]
    assert "Stopped at readiness:" in rig.text
    assert "check" not in labels(rig.runner)


def health_answers(*statuses: str) -> Answer:
    remaining = list(statuses)

    def answer(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        return ok((remaining.pop(0) if len(remaining) > 1 else remaining[0]) + "\n")

    return answer


def is_health_inspect(argv: Sequence[str]) -> bool:
    return "{{.State.Health.Status}}" in argv


def image_after_up(service_label: str, *, recreated: bool = True) -> list[tuple[Predicate, Answer]]:
    """Digest listing for running containers: the old ref until ``up`` runs, then the new one."""
    state = {"up": False}

    def mark(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        state["up"] = True
        return fail(1)

    def digests(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        if state["up"] and recreated:
            return ok(running_json(PYTHON_REF, revision=SHA))
        return ok(running_json(RUNNING_REF))

    return [
        (lambda argv: step_label(argv) == service_label, mark),
        (lambda argv: RUNNING_FORMAT in argv, digests),
    ]


def test_slow_start_counts_as_success_once_healthy(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(
        overrides=[
            *image_after_up("up gateway"),
            (is_health_inspect, health_answers("starting", "starting", "healthy")),
        ]
    )
    assert rig.apply() == 0
    seen = labels(rig.runner)
    assert seen[seen.index("up gateway") + 1 :][:3] == ["up worker", "up delivery", "up frontend"]
    inspects = [a for a in rig.runner.argvs if is_health_inspect(a)]
    assert (
        inspects
        == [
            [
                "docker",
                "--host",
                sr.SOCKET,
                "inspect",
                "--format",
                "{{.State.Health.Status}}",
                "csa-staging-gateway-1",
            ]
        ]
        * 3
    )
    assert rig.sleeps == [5, 5]
    assert "gateway reported unhealthy; waiting for it to become healthy" in rig.lines


def test_healthy_old_image_after_failed_up_is_not_a_recreate(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(
        overrides=[
            *image_after_up("up worker", recreated=False),
            (is_health_inspect, health_answers("healthy")),
        ]
    )
    assert rig.apply() == 1
    assert "Stopped at recreate worker:" in rig.text
    assert "not healthy on the new image" in rig.text
    assert "up delivery" not in labels(rig.runner)
    assert "finished" not in rig.text
    assert sum(rig.sleeps) <= 300


def test_missing_container_after_failed_up_fails_without_waiting(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(
        overrides=[
            (lambda argv: step_label(argv) == "up gateway", fail(1)),
            (is_health_inspect, fail(1)),
        ]
    )
    assert rig.apply() == 1
    assert rig.sleeps == []
    assert "Stopped at recreate gateway: gateway container not found after up failed" in rig.text
    assert "up worker" not in labels(rig.runner)


def test_never_healthy_fails_after_timeout(rig_factory: Callable[..., ApplyRig]) -> None:
    rig = rig_factory(
        **MIGRATING,
        overrides=[
            (lambda argv: step_label(argv) == "up gateway", fail(1)),
            (is_health_inspect, health_answers("unhealthy")),
        ],
    )
    assert rig.apply(["0486", "0487"]) == 1
    assert sum(rig.sleeps) <= 300
    assert set(rig.sleeps) == {5}
    assert "Stopped at recreate gateway:" in rig.text
    assert (
        "gateway, frontend, worker and delivery: gateway did not start on the new image; "
        "worker, delivery and frontend are still stopped; the console is unavailable"
    ) in rig.lines
    assert "Rollback images:" not in rig.text
    assert RUNNING_REF not in rig.text
    assert "Re-run plan, then apply" in rig.text
    assert "up worker" not in labels(rig.runner)


def test_recreate_failure_after_a_migration_says_fix_forward(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(
        **MIGRATING,
        overrides=[
            (lambda argv: step_label(argv) == "up worker", fail(1)),
            (is_health_inspect, health_answers("unhealthy")),
        ],
    )
    assert rig.apply(["0486", "0487"]) == 1
    assert "Stopped at recreate worker:" in rig.text
    assert (
        "gateway, frontend, worker and delivery: gateway is up on the new images; "
        "worker did not start on the new image; delivery and frontend are still stopped; "
        "the console is unavailable"
    ) in rig.lines
    assert (
        "Migrations were applied: 0486, 0487. The previous images fail /readyz against this "
        "schema, so do not roll back; fix forward, then re-run plan and apply."
    ) in rig.lines
    assert "Rollback images:" not in rig.text
    assert "To roll back" not in rig.text
    assert RESTART not in rig.text
    assert rig.lines[-1] == RERUN


def test_apply_output_has_no_secret_values(
    rig_factory: Callable[..., ApplyRig], capsys: pytest.CaptureFixture[str]
) -> None:
    rig = rig_factory(**MIGRATING, secrets=("staging_owner_dsn", "staging_model_key"))
    assert rig.apply(["0486", "0487"]) == 0
    seen = [*rig.lines]
    for argv, env, text in rig.runner.calls:
        seen += [*argv, *(env or {}).values(), text or ""]
    captured = capsys.readouterr()
    seen += [captured.out, captured.err]
    assert not any("SYNTHETIC-ONLY" in item for item in seen)
    assert not any(str(rig.secret_dir) in line for line in rig.lines)


def test_main_apply_wires_migrations_to_apply(
    rollout_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rig = ApplyRig(tmp_path, rollout_env, monkeypatch, **MIGRATING)
    argv = ["apply", "--sha", SHA, "--migrations", "0486,0487"]
    assert sr.main(argv, runner=rig.runner) == 0
    assert labels(rig.runner).count("migrate") == 1


def test_main_apply_refuses_a_wrong_confirmation(
    rollout_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rig = ApplyRig(tmp_path, rollout_env, monkeypatch, **MIGRATING)
    assert sr.main(["apply", "--sha", SHA], runner=rig.runner) == 1
    assert "Stopped at confirm:" in capsys.readouterr().out
    assert labels(rig.runner) == ["select"]


def test_main_apply_requires_sha(
    rollout_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rig = ApplyRig(tmp_path, rollout_env, monkeypatch)
    with pytest.raises(SystemExit) as caught:
        sr.main(["apply"], runner=rig.runner)
    assert caught.value.code == 2
    assert rig.runner.calls == []


# --- one failure contract ------------------------------------------------------------------


def _listing(case: str) -> str:
    good = {"name": DIGEST_A, "metadata": {"container": {"tags": [SHA]}}}
    return json.dumps(
        {
            "non-dict entry": ["text", good],
            "null metadata": [{"name": DIGEST_A, "metadata": None}],
            "null tags": [{"name": DIGEST_A, "metadata": {"container": {"tags": None}}}],
            "non-string name": [{"name": 5, "metadata": {"container": {"tags": [SHA]}}}],
        }[case]
    )


@pytest.mark.parametrize(
    "case", ["non-dict entry", "null metadata", "null tags", "non-string name"]
)
def test_malformed_gh_listing_stops_cleanly(
    case: str, rollout_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = staging_runner()
    runner.rules.insert(0, (has("csa-python/versions"), ok(_listing(case))))
    assert sr.main(["plan"], runner=runner) == 1
    assert "Stopped at plan: gh returned an unexpected" in capsys.readouterr().out


def test_malformed_gh_run_listing_stops_cleanly(
    rollout_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = staging_runner(published=False)
    runner.rules.insert(0, (has("run list"), ok(json.dumps(["text", {"headSha": SHA}]))))
    assert sr.main(["plan"], runner=runner) == 1
    assert "Stopped at plan: gh returned an unexpected run listing" in capsys.readouterr().out


def test_unreadable_flags_file_stops_cleanly(
    tmp_path: Path, rig_factory: Callable[..., ApplyRig]
) -> None:
    rig = rig_factory()
    (rig.repo / "deploy" / "staging-rollout.env").unlink()
    assert rig.apply() == 1
    assert "Stopped at flags: cannot read staging-rollout.env" in rig.text
    assert labels(rig.runner) == []
    with pytest.raises(RolloutError) as caught:
        sr.load_flags(tmp_path)  # a directory is unreadable too
    assert caught.value.step == "flags"


def test_missing_binary_is_a_rollout_error() -> None:
    with pytest.raises(RolloutError) as caught:
        sr.SubprocessRunner().run(["csa-no-such-binary-xyz", "--version"])
    assert caught.value.step == "csa-no-such-binary-xyz"
    assert caught.value.message == "csa-no-such-binary-xyz not found"


def test_missing_binary_stops_cleanly_through_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_such_file(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(sr.subprocess, "run", no_such_file)
    assert sr.main(["plan"]) == 1
    assert capsys.readouterr().out.startswith("Stopped at git: git not found")


FORBIDDEN_SQL = (
    "insert",
    "update",
    "delete",
    "alter",
    "drop",
    "create",
    "truncate",
    "grant",
    "revoke",
)
REPO = Path(__file__).resolve().parent.parent


def _strip_sql_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


def test_check_files_are_read_only_selects() -> None:
    files = sorted(sr.CHECKS_DIR.glob("*.sql"))
    assert files, "expected at least the 0485 check file"
    for path in files:
        text = path.read_text(encoding="utf-8")
        header = text.splitlines()[0]
        assert header.startswith("--"), path.name
        assert "read-only" in text.split("\n\n", 1)[0].lower(), path.name
        body = _strip_sql_comments(text).lower()
        assert body.strip().startswith("select"), path.name
        for word in FORBIDDEN_SQL:
            assert not re.search(rf"\b{word}\b", body), f"{path.name} contains {word}"


def test_0485_check_keeps_the_scrub_predicates() -> None:
    text = (sr.CHECKS_DIR / "0485.sql").read_text(encoding="utf-8")
    assert "kind = 'email_export'" in text
    assert "Save email draft to Gmail" in text
    assert "chr(8220)" in text
    assert "AS old_rows" in text


def test_runbook_points_at_the_script_and_the_live_checkout() -> None:
    text = (REPO / "deploy" / "STAGING_PIPELINE.md").read_text(encoding="utf-8")
    assert "scripts.staging_rollout plan" in text
    assert "deploy/rollout_checks/0485.sql" in text
    assert "/home/amsyrzmi/CSAV2" in text
    assert "CSAV2-seed" not in text
    assert "apply --sha" in text
    for needle in (
        "Nothing on staging was changed.",
        "To bring the previous version back:",
        "fix forward",
        "To roll back:",
        "stopped until",
    ):
        assert needle in text, needle


def test_seed_runbook_points_at_the_live_checkout() -> None:
    text = (REPO / "deploy" / "STAGING_SEED.md").read_text(encoding="utf-8")
    assert "CSAV2-seed" not in text
    assert "cd /home/amsyrzmi/CSAV2 " in text


def test_skill_requires_plan_then_owner_go_then_apply() -> None:
    text = (REPO / ".claude" / "skills" / "staging-rollout" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    frontmatter = text.split("---", 2)[1]
    assert "name: staging-rollout" in frontmatter
    assert "description: Use when the owner asks to roll out, deploy or update staging" in (
        frontmatter
    )
    for needle in ("plan", "apply", "--ff-only", "--sha", "verbatim"):
        assert needle in text
    assert text.index("plan") < text.index("apply")


# --- plan refusals added in the final review ------------------------------------------------


def test_target_refuses_untracked_migration_files(tmp_path: Path) -> None:
    runner = staging_runner()
    runner.rules.insert(0, (has("-- db/migrations"), ok("?? db/migrations/0490_new.sql\n")))
    with pytest.raises(RolloutError) as caught:
        sr.target_sha(runner, tmp_path)
    assert caught.value.step == "plan"
    assert caught.value.message == "db/migrations has uncommitted or untracked files"
    assert [
        "git",
        "-C",
        str(tmp_path),
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        "db/migrations",
    ] in runner.argvs


def test_bad_migration_filename_is_a_plan_stop(
    rollout_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.migrate import MigrationError

    def broken() -> list[tuple[str, Path]]:
        raise MigrationError("migration 'x.sql' has an invalid version")

    monkeypatch.setattr(sr, "discover_migrations", broken)
    paths = make_secret_files(rollout_env, "staging_owner_dsn")
    assert sr.main(["plan"], runner=staging_runner(secret_files=paths)) == 1
    assert "Stopped at plan: migration 'x.sql' has an invalid version" in capsys.readouterr().out


def test_running_image_reads_digest_and_revision_with_the_documented_argv() -> None:
    image_id = "sha256:" + "e" * 64
    runner = FakeRunner(
        [
            (has("inspect --format {{.Image}}"), ok(image_id + "\n")),
            (has("image inspect"), ok(running_json(RUNNING_REF))),
        ]
    )
    running = sr.running_images(runner, {})
    assert {service: (r.ref, r.revision) for service, r in running.items() if r} == {
        service: (RUNNING_REF, PREV_SHA) for service in sr.APP_SERVICES
    }
    docker = ["docker", "--host", sr.SOCKET]
    assert runner.argvs[:2] == [
        [*docker, "inspect", "--format", "{{.Image}}", "csa-staging-gateway-1"],
        [*docker, "image", "inspect", "--format", RUNNING_FORMAT, image_id],
    ]


# --- apply pins the planned SHA -------------------------------------------------------------


MUTATING_LABELS = {"pull python", "pull frontend", "stop", "migrate", "check"}


def is_mutating(label: str) -> bool:
    return label in MUTATING_LABELS or label.startswith("up ")


def test_apply_refuses_a_sha_other_than_head(rig_factory: Callable[..., ApplyRig]) -> None:
    rig = rig_factory(**MIGRATING)
    assert rig.apply(["0486", "0487"], sha=PLANNED_SHA) == 1
    assert labels(rig.runner) == []
    assert "Stopped at confirm: HEAD changed since plan (9f8e7d6 → a1b2c3d); run plan again" in (
        rig.lines
    )
    assert not any(argv[0] == "gh" for argv in rig.runner.argvs)


def test_apply_prints_its_target_before_the_first_change(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(**MIGRATING)
    assert rig.apply(["0486", "0487"]) == 0
    texts = [line for line, _ in rig.marks]
    start = texts.index(f"Target: {SHA}")
    assert texts[start : start + 4] == [
        f"Target: {SHA}",
        f"  python: {PYTHON_REF}",
        f"  frontend: {FRONTEND_REF}",
        "Pending migrations: 0486, 0487",
    ]
    calls_before = rig.marks[start + 3][1]
    before = [step_label(argv) for argv, _, _ in rig.runner.calls[:calls_before]]
    assert not any(is_mutating(label) for label in before if label)
    assert start < texts.index("Pulling and verifying images…")


def test_apply_image_only_target_says_no_migrations(rig_factory: Callable[..., ApplyRig]) -> None:
    rig = rig_factory()
    assert rig.apply() == 0
    assert "Pending migrations: none" in rig.lines


def test_apply_announces_each_step_as_it_starts(rig_factory: Callable[..., ApplyRig]) -> None:
    rig = rig_factory(**MIGRATING)
    assert rig.apply(["0486", "0487"]) == 0
    announced = [line for line in rig.lines if line.endswith("…")]
    assert announced == [
        "Checking compose config…",
        "Pulling and verifying images…",
        "Stopping gateway, frontend, worker, delivery…",
        "Applying migrations 0486, 0487…",
        "Confirming migrations recorded…",
        "Recreating gateway…",
        "Recreating worker…",
        "Recreating delivery…",
        "Recreating frontend…",
        "Checking readiness…",
        "Running check 0485.sql…",
    ]


# --- recovery depends on how far apply got --------------------------------------------------


@pytest.mark.parametrize("case", ["preflight", "pull", "confirm"])
def test_failure_before_any_change_says_nothing_changed(
    rig_factory: Callable[..., ApplyRig], case: str
) -> None:
    if case == "preflight":
        rig = rig_factory(secrets=("staging_owner_dsn", "absent_one"))
        code = rig.apply()
    elif case == "pull":
        rig = rig_factory(overrides=[(lambda argv: step_label(argv) == "pull frontend", fail(1))])
        code = rig.apply()
    else:
        rig = rig_factory(**MIGRATING)
        code = rig.apply(["0486"])
    assert code == 1
    assert rig.lines[-2:] == ["Nothing on staging was changed.", RERUN]
    assert "Rollback images" not in rig.text
    assert RESTART not in rig.text


def test_failed_stop_runs_no_migrate_and_offers_the_restart(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(**MIGRATING, overrides=[(lambda argv: step_label(argv) == "stop", fail(1))])
    assert rig.apply(["0486", "0487"]) == 1
    seen = labels(rig.runner)
    assert "migrate" not in seen
    assert seen[-1] == "stop"
    assert "Stopped at stop: compose stop failed (exit 1)" in rig.text
    assert rig.lines[rig.lines.index("To bring the previous version back:") + 1] == RESTART
    assert "Rollback images" not in rig.text


def test_migrate_failure_with_unreadable_schema_says_state_unknown(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    state = {"migrated": False}

    def migrate(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        state["migrated"] = True
        return Completed(1, "", "")

    def select(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        return fail(2) if state["migrated"] else ok("0484\n0485\n")

    rig = rig_factory(
        **MIGRATING,
        overrides=[
            (lambda argv: step_label(argv) == "migrate", migrate),
            (lambda argv: step_label(argv) == "select", select),
        ],
    )
    assert rig.apply(["0486", "0487"]) == 1
    assert "migration state is unknown" in rig.text
    assert "check schema_migrations before restarting" in rig.text
    assert "Rollback images" not in rig.text
    assert "Nothing on staging was changed." not in rig.text


def test_partly_recorded_migration_says_fix_forward_without_rollback_images(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(**MIGRATING, recorded=("0484", "0485", "0486"))
    assert rig.apply(["0486", "0487"]) == 1
    assert (
        "Migrations were applied: 0486. The previous images fail /readyz against this schema, "
        "so do not roll back; fix forward, then re-run plan and apply."
    ) in rig.lines
    assert "Rollback images" not in rig.text
    assert RUNNING_REF not in rig.text
    assert RESTART not in rig.text


def test_image_only_recreate_failure_offers_the_previous_sha(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(
        overrides=[
            *image_after_up("up worker", recreated=False),
            (is_health_inspect, health_answers("unhealthy")),
        ]
    )
    assert rig.apply() == 1
    assert (
        "gateway, frontend, worker and delivery: gateway is up on the new images; "
        "worker did not start on the new image; delivery and frontend still run the previous "
        "images"
    ) in rig.lines
    start = rig.lines.index("Rollback images:")
    assert rig.lines[start + 1] == f"  gateway: {RUNNING_REF} (revision {PREV_SHA})"
    assert (
        f"To roll back: git checkout {PREV_SHA} in /home/amsyrzmi/CSAV2, run plan and apply, "
        "then git checkout main."
    ) in rig.lines
    assert "Migrations were applied" not in rig.text
    assert rig.lines[-1] == RERUN


def test_image_only_failure_without_running_containers_says_none_known(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(
        running=False,
        overrides=[
            (lambda argv: step_label(argv) == "up gateway", fail(1)),
            (is_health_inspect, fail(1)),
        ],
    )
    assert rig.apply() == 1
    start = rig.lines.index("Rollback images:")
    assert rig.lines[start + 1] == "  none known (not running)"
    assert "To roll back" not in rig.text


def test_rollback_images_of_mixed_revisions_ask_to_pick_one(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    frontend_old = "ghcr.io/amsyrzmi/csa-frontend@sha256:" + "f" * 64
    seen = {"inspects": 0}

    def digests(argv: Sequence[str], env: Mapping[str, str] | None) -> Completed:
        seen["inspects"] += 1  # plan reads gateway, worker, delivery, frontend in that order
        if seen["inspects"] == 4:
            return ok(running_json(frontend_old, revision=OTHER_SHA))
        return ok(running_json(RUNNING_REF))

    rig = rig_factory(
        overrides=[
            (lambda argv: RUNNING_FORMAT in argv, digests),
            (lambda argv: step_label(argv) == "ready", fail(1)),
        ]
    )
    assert rig.apply() == 1
    assert f"  frontend: {frontend_old} (revision {OTHER_SHA})" in rig.lines
    assert f"  gateway: {RUNNING_REF} (revision {PREV_SHA})" in rig.lines
    assert not any(line.startswith("To roll back:") for line in rig.lines)
    assert any("pick one" in line for line in rig.lines)


def test_readiness_failure_names_the_state_and_offers_rollback(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(overrides=[(lambda argv: step_label(argv) == "ready", fail(1))])
    assert rig.apply() == 1
    assert "services are up on the new images but /readyz is failing" in rig.lines
    assert "Rollback images:" in rig.lines
    assert f"To roll back: git checkout {PREV_SHA}" in rig.text


def test_readiness_failure_after_a_migration_says_fix_forward(
    rig_factory: Callable[..., ApplyRig],
) -> None:
    rig = rig_factory(**MIGRATING, overrides=[(lambda argv: step_label(argv) == "ready", fail(1))])
    assert rig.apply(["0486", "0487"]) == 1
    assert "services are up on the new images but /readyz is failing" in rig.lines
    assert any(line.startswith("Migrations were applied: 0486, 0487.") for line in rig.lines)
    assert "Rollback images" not in rig.text


def test_failed_check_says_investigate_then_rerun(rig_factory: Callable[..., ApplyRig]) -> None:
    rig = rig_factory(overrides=[(lambda argv: step_label(argv) == "check", ok("3\n"))])
    assert rig.apply() == 1
    assert (
        "Investigate what the check found before the console is used again, "
        "then re-run plan and apply."
    ) in rig.lines
    assert "Rollback images" not in rig.text
    assert "Nothing on staging was changed." not in rig.text
