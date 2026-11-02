"""Plan and apply a staging rollout of the published images.

``plan`` is read-only: it resolves the checkout's HEAD to published image digests,
reads what staging runs and which migrations it has applied, checks that every
declared secret file exists, and prints the steps ``apply`` would take.
``apply`` re-derives that plan, refuses if HEAD or the pending migrations differ from
what ``plan`` printed, then pulls, migrates, recreates and checks, stopping at the first
failure with recovery lines that match how far it got.

Every external command is an argv list sent through a ``Runner``, so tests swap in
a fake. Secret files are only ever checked with ``Path.exists``; nothing under the
secret directory is opened, read or printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from scripts.migrate import MigrationError, discover_migrations

REPO_ROOT = Path(__file__).resolve().parent.parent
SOCKET = "unix:///var/run/docker.sock"
SECRET_DIR = Path("/srv/csa-staging/secrets")
PROJECT = "csa-staging"
APP_SERVICES = ("gateway", "worker", "delivery", "frontend")  # recreate order
SERVICE_APP = {
    "gateway": "python",
    "worker": "python",
    "delivery": "python",
    "frontend": "frontend",
}
STOP_SERVICES = ("gateway", "frontend", "worker", "delivery")
FLAGS_FILE = "deploy/staging-rollout.env"
FLAG_KEYS = frozenset(
    {
        "CSA_STAGING_CONSOLE_ORIGIN",
        "CSA_STAGING_HOOKS_ORIGIN",
        "CSA_STAGING_WORKSPACE_ASSISTANT",
        "CSA_STAGING_GATEWAY_MODEL_SECRET",
        "CSA_STAGING_INVITE_MAIL",
        "CSA_STAGING_EMAIL_ASSISTANT",
    }
)
CHECKS_DIR = REPO_ROOT / "deploy" / "rollout_checks"
MIGRATION_APPROVAL = ("CSA_STAGING_MIGRATION_APPROVAL", "approved-isolated-staging")
READY_PROBE = (
    "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=5)"
)
READY_TRIES = 5
READY_PAUSE = 3
HEALTH_POLL = 5
HEALTH_WAIT = 300
STOPPED_NAMES = "gateway, frontend, worker and delivery"
ALL_STOPPED = f"{STOPPED_NAMES} are stopped"
LIVE_CHECKOUT = "/home/amsyrzmi/CSAV2"
REVISION_LABEL = "org.opencontainers.image.revision"
# One JSON object per running image: its repo digests and its labels (for the revision).
RUNNING_FORMAT = '{"digests": {{json .RepoDigests}}, "labels": {{json .Config.Labels}} }'
RESTART_COMMAND = " ".join(
    ["docker", "--host", SOCKET, "start", *(f"{PROJECT}-{service}-1" for service in STOP_SERVICES)]
)
READY_STATE = "services are up on the new images but /readyz is failing"
RERUN_HINT = "Re-run plan, then apply, once the cause is fixed."

GHCR_OWNER = "ghcr.io/amsyrzmi"
APPS = ("python", "frontend")
IMAGE_ENV = {"python": "CSA_STAGING_PYTHON_IMAGE", "frontend": "CSA_STAGING_FRONTEND_IMAGE"}
PUBLISH_WORKFLOW = "publish-staging-images.yml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
APPLY_COMMAND = ".venv/bin/python -m scripts.staging_rollout apply"
DOWNTIME = (
    "Downtime: console unavailable and bot replies delayed (queued, not lost) "
    "until the services are back."
)


class RolloutError(Exception):
    """A rollout stop: which step, why, and optionally what state staging is in."""

    def __init__(self, step: str, message: str, state: str | None = None) -> None:
        super().__init__(message)
        self.step = step
        self.message = message
        self.state = state


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        input: str | None = None,
    ) -> Completed: ...


class SubprocessRunner:
    """The real runner; ``cwd`` lets relative compose file paths resolve to the repo."""

    def __init__(self, cwd: Path | None = None) -> None:
        self.cwd = cwd

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        input: str | None = None,
    ) -> Completed:
        binary = argv[0]
        try:
            result = subprocess.run(
                list(argv),
                env=env,
                input=input,
                capture_output=True,
                text=True,
                check=False,
                cwd=self.cwd,
            )
        except FileNotFoundError as error:
            raise RolloutError(binary, f"{binary} not found") from error
        except OSError as error:
            raise RolloutError(binary, f"could not run {binary}: {error.strerror}") from error
        return Completed(result.returncode, result.stdout, result.stderr)


def load_flags(path: Path) -> dict[str, str]:
    """Read the non-secret switches: exactly the six allowed keys, each once."""
    flags: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise RolloutError("flags", f"cannot read {path.name}") from error
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator:
            raise RolloutError("flags", f"{path.name} line {number} has no '='")
        if key not in FLAG_KEYS:
            raise RolloutError("flags", f"{path.name} line {number}: unknown key {key}")
        if key in flags:
            raise RolloutError("flags", f"{path.name} line {number}: duplicate key {key}")
        flags[key] = value.strip()
    missing = sorted(FLAG_KEYS - flags.keys())
    if missing:
        raise RolloutError("flags", f"{path.name} is missing: {', '.join(missing)}")
    return flags


def docker_base() -> list[str]:
    return ["docker", "--host", SOCKET]


def compose_base() -> list[str]:
    return [
        *docker_base(),
        "compose",
        "--env-file",
        "/dev/null",
        "--project-name",
        PROJECT,
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


def build_env(
    flags: Mapping[str, str],
    images: Mapping[str, str],
    *,
    docker_config: Path,
    secret_dir: Path = SECRET_DIR,
) -> dict[str, str]:
    """The whole environment for docker and compose; nothing else is inherited."""
    env = {name: os.environ[name] for name in ("PATH", "HOME") if name in os.environ}
    env |= {
        "NO_LOCAL_ENV": "1",
        "ENV": "ci",
        "COMPOSE_PROFILES": "",
        "CSA_STAGING_PROJECT": PROJECT,
        "CSA_STAGING_SECRET_DIR": str(secret_dir),
        "DOCKER_CONFIG": str(docker_config),
    }
    env |= {IMAGE_ENV[app]: images[app] for app in APPS}
    env |= flags
    return env


def _first_line(text: str) -> str:
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else ""


def target_sha(runner: Runner, repo: Path) -> str:
    """HEAD of a clean checkout that is already on origin/main. Never fetches."""
    head = runner.run(["git", "-C", str(repo), "rev-parse", "HEAD"])
    sha = head.stdout.strip()
    if head.returncode != 0 or not SHA_RE.match(sha):
        raise RolloutError("plan", "could not read HEAD of the checkout")
    status = runner.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"])
    if status.returncode != 0 or status.stdout.strip():
        raise RolloutError("plan", "the checkout has tracked changes; use a clean main checkout")
    # An untracked migration file would be planned and applied without being on main.
    migrations = runner.run(
        [
            "git",
            "-C",
            str(repo),
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            "db/migrations",
        ]
    )
    if migrations.returncode != 0 or migrations.stdout.strip():
        raise RolloutError("plan", "db/migrations has uncommitted or untracked files")
    ancestor = runner.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", "HEAD", "origin/main"]
    )
    if ancestor.returncode != 0:
        raise RolloutError(
            "plan", "HEAD is not on origin/main; run git pull --ff-only origin main first"
        )
    return sha


def _gh(runner: Runner, argv: Sequence[str]) -> str:
    result = runner.run(argv)  # no env: gh keeps the user's login
    if result.returncode != 0:
        detail = _first_line(result.stderr) or f"exit {result.returncode}"
        raise RolloutError("plan", f"gh failed: {detail}")
    return result.stdout


def _gh_json(runner: Runner, argv: Sequence[str]) -> object:
    try:
        return json.loads(_gh(runner, argv))
    except json.JSONDecodeError as error:
        raise RolloutError("plan", "gh returned unreadable output") from error


def _version_tags(version: object) -> list[object]:
    """The tags of one package version; any other shape is an unexpected listing."""
    metadata = version.get("metadata") if isinstance(version, dict) else None
    container = metadata.get("container") if isinstance(metadata, dict) else None
    tags = container.get("tags") if isinstance(container, dict) else None
    if not isinstance(tags, list):
        raise RolloutError("plan", "gh returned an unexpected package listing")
    return tags


def published_images(runner: Runner, sha: str) -> dict[str, str] | None:
    """Digest refs for the version of each package tagged with the full SHA."""
    images: dict[str, str] = {}
    for app in APPS:
        versions = _gh_json(
            runner,
            [
                "gh",
                "api",
                f"/users/amsyrzmi/packages/container/csa-{app}/versions?per_page=100",
            ],
        )
        if not isinstance(versions, list):
            raise RolloutError("plan", "gh returned an unexpected package listing")
        match = None
        for version in versions:
            if sha in _version_tags(version):
                match = version
                break
        if match is None:
            return None
        name = match.get("name")
        if not isinstance(name, str):
            raise RolloutError("plan", "gh returned an unexpected package listing")
        if not DIGEST_RE.match(name):
            raise RolloutError("plan", f"csa-{app} version for {sha[:7]} has a malformed digest")
        images[app] = f"{GHCR_OWNER}/csa-{app}@{name}"
    return images


def publish_state(runner: Runner, sha: str) -> str:
    """Where the newest publish run for this SHA stands."""
    runs = _gh_json(
        runner,
        [
            "gh",
            "run",
            "list",
            "--workflow",
            PUBLISH_WORKFLOW,
            "--limit",
            "20",
            "--json",
            "databaseId,headSha,status,conclusion",
        ],
    )
    if not isinstance(runs, list):
        raise RolloutError("plan", "gh returned an unexpected run listing")
    if not all(isinstance(run, dict) for run in runs):
        raise RolloutError("plan", "gh returned an unexpected run listing")
    matching = [run for run in runs if run.get("headSha") == sha]
    if not matching:
        return "no publish run found"
    if not all(isinstance(run.get("databaseId"), int) for run in matching):
        raise RolloutError("plan", "gh returned an unexpected run listing")
    newest = max(matching, key=lambda run: run["databaseId"])
    status = newest.get("status")
    if status == "waiting":
        return "waiting for approval"
    if status in {"pending", "requested", "queued", "in_progress"}:
        return "in progress"
    return f"finished: {newest.get('conclusion') or status}"


@dataclass(frozen=True)
class RunningImage:
    """The image of an app container: its ghcr digest ref and the commit it was built from."""

    ref: str
    revision: str | None


def running_images(runner: Runner, env: Mapping[str, str]) -> dict[str, RunningImage | None]:
    """The image of each app container (running or stopped), or None when it cannot be read."""
    running: dict[str, RunningImage | None] = {}
    for service in APP_SERVICES:
        running[service] = _running_image(runner, env, service)
    return running


def _running_image(runner: Runner, env: Mapping[str, str], service: str) -> RunningImage | None:
    inspect = runner.run(
        [*docker_base(), "inspect", "--format", "{{.Image}}", f"{PROJECT}-{service}-1"], env=env
    )
    image_id = inspect.stdout.strip()
    if inspect.returncode != 0 or not image_id:
        return None
    described = runner.run(
        [*docker_base(), "image", "inspect", "--format", RUNNING_FORMAT, image_id], env=env
    )
    if described.returncode != 0:
        return None
    try:
        data = json.loads(described.stdout)
    except json.JSONDecodeError:
        return None
    entries = data.get("digests") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    labels = data.get("labels")
    revision = labels.get(REVISION_LABEL) if isinstance(labels, dict) else None
    if not isinstance(revision, str) or not SHA_RE.match(revision):
        revision = None
    for entry in entries:
        if isinstance(entry, str) and entry.startswith(f"{GHCR_OWNER}/"):
            return RunningImage(entry, revision)
    return None


def applied_versions(runner: Runner, env: Mapping[str, str]) -> list[str]:
    result = runner.run(
        [
            *compose_base(),
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
            "-c",
            "SELECT version FROM schema_migrations ORDER BY version",
        ],
        env=env,
    )
    if result.returncode != 0:
        raise RolloutError(
            "plan", f"could not read schema_migrations from staging (exit {result.returncode})"
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def pending_versions(checkout: Sequence[str], applied: Sequence[str]) -> list[str]:
    ahead = sorted(set(applied) - set(checkout))
    if ahead:
        raise RolloutError(
            "plan",
            f"staging has migrations this checkout lacks: {', '.join(ahead)}; "
            "update the checkout before rolling out",
        )
    done = set(applied)
    return [version for version in checkout if version not in done]


def secret_paths(runner: Runner, env: Mapping[str, str]) -> dict[str, Path]:
    """Secret name to the file compose will mount, from the rendered config."""
    result = runner.run(
        [*compose_base(), "--profile", "migration-approved", "config", "--format", "json"],
        env=env,
    )
    if result.returncode != 0:
        raise RolloutError("plan", f"compose config failed (exit {result.returncode})")
    try:
        secrets = json.loads(result.stdout).get("secrets", {})
        return {name: Path(spec["file"]) for name, spec in secrets.items() if "file" in spec}
    except (json.JSONDecodeError, AttributeError, TypeError) as error:
        raise RolloutError("plan", "compose config output was unreadable") from error


def missing_secrets(paths: Mapping[str, Path]) -> list[str]:
    """Names whose file is absent. Existence only; a path that cannot be checked is missing."""
    missing = []
    for name, path in paths.items():
        try:
            exists = path.exists()
        except OSError:
            exists = False
        if not exists:
            missing.append(name)
    return sorted(missing)


def check_files(directory: Path = CHECKS_DIR) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.sql"))


@dataclass(frozen=True)
class Plan:
    sha: str
    images: dict[str, str]
    rollback: dict[str, RunningImage | None]
    pending: list[str]
    missing: list[str]
    checks: list[str]
    steps: list[str]
    env: dict[str, str] = field(repr=False)  # non-secret flags and image refs for apply


def plan_steps(pending: Sequence[str], checks: Sequence[str]) -> list[str]:
    steps = ["pull and verify images"]
    if pending:
        steps += [
            f"stop {', '.join(STOP_SERVICES)}",
            f"apply migrations {', '.join(pending)}",
            "confirm migrations recorded",
        ]
    steps += [
        f"recreate {', '.join(APP_SERVICES)}",
        "check readiness",
        f"run checks: {', '.join(checks) if checks else 'none'}",
    ]
    return steps


def build_plan(
    runner: Runner,
    repo: Path,
    *,
    docker_config: Path,
    secret_dir: Path = SECRET_DIR,
    checks_dir: Path = CHECKS_DIR,
    expected_sha: str | None = None,
) -> Plan:
    if expected_sha is not None and not SHA_RE.match(expected_sha):
        raise RolloutError("confirm", "--sha must be the full 40-character SHA that plan printed")
    sha = target_sha(runner, repo)
    if expected_sha is not None and expected_sha != sha:
        raise RolloutError(
            "confirm", f"HEAD changed since plan ({expected_sha[:7]} → {sha[:7]}); run plan again"
        )
    images = published_images(runner, sha)
    if images is None:
        raise RolloutError(
            "plan", f"images for {sha[:7]} are not published yet ({publish_state(runner, sha)})"
        )
    flags = load_flags(repo / FLAGS_FILE)
    env = build_env(flags, images, docker_config=docker_config, secret_dir=secret_dir)
    # Secrets first: a host with a missing file lists it instead of failing on a staging read.
    missing = missing_secrets(secret_paths(runner, env))
    rollback = running_images(runner, env)
    # Migrations come from this module's checkout, so main always passes REPO_ROOT.
    try:
        checkout = [version for version, _ in discover_migrations()]
    except MigrationError as error:
        raise RolloutError("plan", str(error)) from error
    pending = pending_versions(checkout, applied_versions(runner, env))
    checks = [path.name for path in check_files(checks_dir)]
    steps = plan_steps(pending, checks)
    return Plan(sha, images, rollback, pending, missing, checks, steps, env)


def _pending_line(pending: Sequence[str]) -> str:
    return f"Pending migrations: {', '.join(pending) if pending else 'none'}"


def format_plan(plan: Plan) -> str:
    lines = [f"Target SHA: {plan.sha}", "Images to deploy:"]
    lines += [f"  {app}: {plan.images[app]}" for app in APPS]
    lines.append("Rollback references (current containers):")
    for service in APP_SERVICES:
        current = plan.rollback.get(service)
        lines.append(f"  {service}: {current.ref if current else 'unknown (not running)'}")
    lines.append(_pending_line(plan.pending))
    if plan.missing:
        lines += [
            f"Missing secret files: {', '.join(plan.missing)}",
            "apply will refuse until they exist",
        ]
    lines.append("Steps:")
    lines += [f"  {number}. {step}" for number, step in enumerate(plan.steps, start=1)]
    if plan.pending:
        lines.append(DOWNTIME)
    command = f"{APPLY_COMMAND} --sha {plan.sha}"
    if plan.pending:
        command += f" --migrations {','.join(plan.pending)}"
    lines.append(command)
    return "\n".join(lines)


def parse_migrations(value: str | None) -> list[str]:
    """``"0486,0487"`` -> ``["0486", "0487"]``; ``None`` or empty -> ``[]``."""
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _run_step(
    runner: Runner,
    argv: Sequence[str],
    env: Mapping[str, str],
    step: str,
    what: str,
    state: str | None = None,
    *,
    input: str | None = None,  # noqa: A002
) -> Completed:
    result = runner.run(argv, env=env, input=input)
    if result.returncode != 0:
        raise RolloutError(step, f"{what} failed (exit {result.returncode})", state)
    return result


def _verify_pull(runner: Runner, env: Mapping[str, str], ref: str, sha: str) -> None:
    _run_step(runner, [*docker_base(), "pull", ref], env, "pull", f"docker pull {ref}")
    inspect = _run_step(
        runner,
        [*docker_base(), "image", "inspect", ref],
        env,
        "pull",
        f"docker image inspect {ref}",
    )
    try:
        entry = json.loads(inspect.stdout)[0]
        digests = entry["RepoDigests"]
        revision = entry["Config"]["Labels"]["org.opencontainers.image.revision"]
    except (json.JSONDecodeError, LookupError, TypeError) as error:
        raise RolloutError("pull", f"{ref} has no readable digest or revision label") from error
    if not isinstance(digests, list) or ref not in digests:
        raise RolloutError("pull", f"pulled image does not carry the digest {ref}")
    if revision != sha:
        raise RolloutError("pull", f"{ref} was built from {revision}, expected {sha}")


@dataclass
class _Progress:
    """How far apply got, so a stop can say what changed and how to recover."""

    phase: str = "unchanged"  # unchanged, stopped, recreating, up, checks or check_unread
    recorded: list[str] | None = field(default_factory=list)  # planned versions recorded
    pending: list[str] = field(default_factory=list)
    rollback: Mapping[str, RunningImage | None] = field(default_factory=dict)


def _join(names: Sequence[str]) -> str:
    """``["a", "b", "c"]`` -> ``"a, b and c"``."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _recorded(
    runner: Runner, env: Mapping[str, str], pending: Sequence[str]
) -> tuple[list[str] | None, str]:
    """The planned versions schema_migrations records now, or None with why it is unknown."""
    try:
        applied = applied_versions(runner, env)
    except RolloutError as error:
        return None, error.message
    return [version for version in pending if version in applied], ""


def _migrate_state(pending: Sequence[str], recorded: Sequence[str] | None) -> str:
    if recorded is None:
        return (
            f"{ALL_STOPPED}; schema_migrations could not be read, so the migration state is unknown"
        )
    absent = [version for version in pending if version not in recorded]
    if not recorded:
        if len(pending) == 1:
            return f"{ALL_STOPPED}; {pending[0]} is not recorded in schema_migrations"
        return f"{ALL_STOPPED}; none of {', '.join(pending)} is recorded in schema_migrations"
    if absent:
        return (
            f"{ALL_STOPPED}; schema_migrations records {', '.join(recorded)} "
            f"but not {', '.join(absent)}"
        )
    return f"{ALL_STOPPED}; schema_migrations records {', '.join(recorded)}"


def _migrate(
    runner: Runner,
    env: Mapping[str, str],
    pending: Sequence[str],
    out: Callable[[str], None],
    progress: _Progress,
) -> None:
    out(f"Stopping {', '.join(STOP_SERVICES)}…")
    progress.phase = "stopped"  # a failed stop may still have stopped some of them
    _run_step(
        runner,
        [*compose_base(), "stop", *STOP_SERVICES],
        env,
        "stop",
        "compose stop",
        "some of gateway, frontend, worker and delivery may still be running; check compose ps",
    )
    out(f"Applying migrations {', '.join(pending)}…")
    migrate_env = {**env, MIGRATION_APPROVAL[0]: MIGRATION_APPROVAL[1]}
    result = runner.run(
        [*compose_base(), "--profile", "migration-approved", "run", "--rm", "--no-deps", "migrate"],
        env=migrate_env,
    )
    if result.stdout.strip():
        out(result.stdout.rstrip())
    out(f"migrate exit={result.returncode}")
    if result.returncode != 0:
        # Read what migrate left behind: it decides between restarting and fixing forward.
        progress.recorded, _ = _recorded(runner, env, pending)
        raise RolloutError(
            "migrate",
            f"migrate exited {result.returncode}",
            _migrate_state(pending, progress.recorded),
        )
    out("Confirming migrations recorded…")
    progress.recorded, why = _recorded(runner, env, pending)
    state = _migrate_state(pending, progress.recorded)
    if progress.recorded is None:
        raise RolloutError("migrate", why, state)
    absent = [version for version in pending if version not in progress.recorded]
    if absent:
        raise RolloutError(
            "migrate", f"schema_migrations lacks {', '.join(absent)} after migrate", state
        )


def _recreate_state(service: str, stopped: bool) -> str:
    """What is down when ``service`` failed to come up on its new image."""
    index = APP_SERVICES.index(service)
    done, later = APP_SERVICES[:index], APP_SERVICES[index + 1 :]
    parts = []
    if done:
        parts.append(f"{_join(done)} {'is' if len(done) == 1 else 'are'} up on the new images")
    parts.append(f"{service} did not start on the new image")
    if later and stopped:
        parts.append(f"{_join(later)} {'is' if len(later) == 1 else 'are'} still stopped")
    elif later:
        verb = "runs the previous image" if len(later) == 1 else "run the previous images"
        parts.append(f"{_join(later)} still {verb}")
    if stopped:
        parts.append("the console is unavailable")
    elif service in {"gateway", "frontend"}:
        parts.append("the console may be unavailable")
    return f"{STOPPED_NAMES}: {'; '.join(parts)}"


def _recreate(
    runner: Runner,
    env: Mapping[str, str],
    service: str,
    expected: str,
    sleep: Callable[[float], None],
    out: Callable[[str], None],
    *,
    stopped: bool,
) -> None:
    state = _recreate_state(service, stopped)
    up = runner.run(
        [
            *compose_base(),
            "up",
            "--detach",
            "--wait",
            "--wait-timeout",
            "300",
            "--no-deps",
            "--pull",
            "never",
            service,
        ],
        env=env,
    )
    if up.returncode == 0:
        return
    # A slow start on a loaded host reports unhealthy first and recovers; wait for it. Healthy
    # only counts when the container runs the planned image: a failed up can leave the old one.
    out(f"{service} reported unhealthy; waiting for it to become healthy")
    waited = 0
    while True:
        health = runner.run(
            [
                *docker_base(),
                "inspect",
                "--format",
                "{{.State.Health.Status}}",
                f"{PROJECT}-{service}-1",
            ],
            env=env,
        )
        if health.returncode != 0:
            raise RolloutError(
                f"recreate {service}", f"{service} container not found after up failed", state
            )
        if health.stdout.strip() == "healthy":
            current = _running_image(runner, env, service)
            if current is not None and current.ref == expected:
                return
        if waited >= HEALTH_WAIT:
            raise RolloutError(
                f"recreate {service}",
                f"{service} was not healthy on the new image within {HEALTH_WAIT} s",
                state,
            )
        sleep(HEALTH_POLL)
        waited += HEALTH_POLL


def _await_ready(runner: Runner, env: Mapping[str, str], sleep: Callable[[float], None]) -> None:
    argv = [*docker_base(), "exec", f"{PROJECT}-gateway-1", "python", "-c", READY_PROBE]
    for attempt in range(READY_TRIES):
        if runner.run(argv, env=env).returncode == 0:
            return
        if attempt < READY_TRIES - 1:
            sleep(READY_PAUSE)
    raise RolloutError("readiness", f"/readyz not ready after {READY_TRIES} tries", READY_STATE)


def _run_checks(
    runner: Runner,
    env: Mapping[str, str],
    checks_dir: Path,
    out: Callable[[str], None],
    progress: _Progress,
) -> None:
    for path in check_files(checks_dir):
        out(f"Running check {path.name}…")
        try:
            sql = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            progress.phase = "check_unread"
            raise RolloutError(f"check {path.name}", "cannot read the check file") from error
        result = runner.run(
            [
                *compose_base(),
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
            ],
            env=env,
            input=sql,
        )
        value = result.stdout.strip()
        if result.returncode == 0 and value == "0":
            out(f"check {path.name}: 0")
            continue
        stopped = runner.run([*compose_base(), "stop", *STOP_SERVICES], env=env)
        printed = f"printed {value or 'nothing'}" if result.returncode == 0 else "psql failed"
        raise RolloutError(
            f"check {path.name}",
            f"{printed}, expected 0",
            state=(
                "services stopped; investigate before the console is used again"
                if stopped.returncode == 0
                else f"compose stop failed (exit {stopped.returncode}); {STOPPED_NAMES} "
                "may still be running; stop them by hand"
            ),
        )


def _report_stop(error: RolloutError, out: Callable[[str], None]) -> None:
    out(f"Stopped at {error.step}: {error.message}")
    if error.state:
        out(error.state)


def _report_rollback(
    rollback: Mapping[str, RunningImage | None], out: Callable[[str], None]
) -> None:
    """Image-only rollouts: the images to go back to and the commit that publishes them."""
    out("Rollback images:")
    known = [(service, image) for service, image in rollback.items() if image]
    if not known:
        out("  none known (not running)")
        return
    for service, image in known:
        out(f"  {service}: {image.ref} (revision {image.revision or 'unknown'})")
    revisions = {image.revision for _, image in known}
    if len(revisions) == 1 and None not in revisions:
        out(
            f"To roll back: git checkout {revisions.pop()} in {LIVE_CHECKOUT}, run plan and apply, "
            "then git checkout main."
        )
    else:
        out(
            "The rollback images do not share one known revision: pick one, git checkout it in "
            f"{LIVE_CHECKOUT}, run plan and apply, then git checkout main."
        )


def _report_recovery(progress: _Progress, out: Callable[[str], None]) -> None:
    """Recovery lines for how far apply got; never a rollback once a migration is recorded."""
    if progress.phase == "unchanged":
        out("Nothing on staging was changed.")
        return
    if progress.phase == "check_unread":
        out("The check did not run and the services were not stopped; fix the check file.")
        return
    if progress.phase == "checks":
        out(
            "Investigate what the check found before the console is used again, "
            "then re-run plan and apply."
        )
        return
    if progress.recorded is None:
        out("The migration state is unknown: check schema_migrations before restarting.")
        planned = ", ".join(progress.pending)
        out(f"If none of {planned} is recorded, to bring the previous version back:")
        out(RESTART_COMMAND)
        out("If any is recorded, do not roll back; fix forward, then re-run plan and apply.")
        return
    if progress.recorded:
        out(
            f"Migrations were applied: {', '.join(progress.recorded)}. The previous images fail "
            "/readyz against this schema, so do not roll back; fix forward, then re-run plan "
            "and apply."
        )
        return
    if progress.phase == "stopped":
        out("To bring the previous version back:")
        out(RESTART_COMMAND)
        return
    _report_rollback(progress.rollback, out)


def _print_target(plan: Plan, out: Callable[[str], None]) -> None:
    out(f"Target: {plan.sha}")
    for app in APPS:
        out(f"  {app}: {plan.images[app]}")
    out(_pending_line(plan.pending))


def apply(
    runner: Runner,
    repo: Path,
    confirmed: list[str],
    *,
    sha: str,
    docker_config: Path,
    secret_dir: Path = SECRET_DIR,
    checks_dir: Path = CHECKS_DIR,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> int:
    progress = _Progress()
    try:
        plan = build_plan(
            runner,
            repo,
            docker_config=docker_config,
            secret_dir=secret_dir,
            checks_dir=checks_dir,
            expected_sha=sha,
        )
        env = plan.env
        progress.pending = plan.pending
        progress.rollback = plan.rollback
        if confirmed != plan.pending:
            raise RolloutError("confirm", "pending migrations changed since plan; run plan again")
        if plan.missing:
            raise RolloutError("preflight", f"missing secret files: {', '.join(plan.missing)}")
        _print_target(plan, out)
        out("Checking compose config…")
        _run_step(
            runner, [*compose_base(), "config", "--quiet"], env, "config", "compose config --quiet"
        )
        out("Pulling and verifying images…")
        for app in APPS:
            _verify_pull(runner, env, plan.images[app], plan.sha)
        if plan.pending:
            _migrate(runner, env, plan.pending, out, progress)
        progress.phase = "recreating"
        for service in APP_SERVICES:
            out(f"Recreating {service}…")
            expected = plan.images[SERVICE_APP[service]]
            _recreate(runner, env, service, expected, sleep, out, stopped=bool(plan.pending))
        progress.phase = "up"
        out("Checking readiness…")
        _await_ready(runner, env, sleep)
        progress.phase = "checks"
        _run_checks(runner, env, checks_dir, out, progress)
        status = runner.run(
            [*compose_base(), "ps", "--format", "{{.Service}} {{.Status}}"], env=env
        )
        if status.returncode == 0:
            out(status.stdout.rstrip())
        else:
            out(f"could not list services (compose ps exit {status.returncode})")
        out(f"Rollout of {plan.sha[:7]} finished.")
        return 0
    except RolloutError as error:
        _report_stop(error, out)
        _report_recovery(progress, out)
        out(RERUN_HINT)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts.staging_rollout", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="show what a rollout of HEAD would do (read-only)")
    apply = commands.add_parser("apply", help="roll HEAD out to staging")
    apply.add_argument("--sha", required=True, help="the full target SHA, as plan printed")
    apply.add_argument("--migrations", help="comma-separated pending versions, as plan printed")
    return parser


def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    args = _parser().parse_args(argv)
    runner = runner or SubprocessRunner(cwd=REPO_ROOT)
    docker_config = Path(tempfile.mkdtemp(prefix="csa-rollout-docker-"))
    try:
        docker_config.chmod(0o700)
        if args.command == "apply":
            return apply(
                runner,
                REPO_ROOT,
                parse_migrations(args.migrations),
                sha=args.sha,
                docker_config=docker_config,
                secret_dir=SECRET_DIR,
                checks_dir=CHECKS_DIR,
            )
        plan = build_plan(
            runner,
            REPO_ROOT,
            docker_config=docker_config,
            secret_dir=SECRET_DIR,
            checks_dir=CHECKS_DIR,
        )
    except RolloutError as error:
        _report_stop(error, print)
        return 1
    finally:
        shutil.rmtree(docker_config, ignore_errors=True)
    print(format_plan(plan))
    return 2 if plan.missing else 0


if __name__ == "__main__":
    sys.exit(main())
