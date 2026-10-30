"""Plan (and later apply) a staging rollout of the published images.

``plan`` is read-only: it resolves the checkout's HEAD to published image digests,
reads what staging runs and which migrations it has applied, checks that every
declared secret file exists, and prints the steps ``apply`` would take.

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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from scripts.migrate import discover_migrations

REPO_ROOT = Path(__file__).resolve().parent.parent
SOCKET = "unix:///var/run/docker.sock"
SECRET_DIR = Path("/srv/csa-staging/secrets")
PROJECT = "csa-staging"
APP_SERVICES = ("gateway", "worker", "delivery", "frontend")  # recreate order
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
        result = subprocess.run(
            list(argv),
            env=env,
            input=input,
            capture_output=True,
            text=True,
            check=False,
            cwd=self.cwd,
        )
        return Completed(result.returncode, result.stdout, result.stderr)


def load_flags(path: Path) -> dict[str, str]:
    """Read the non-secret switches: exactly the six allowed keys, each once."""
    flags: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
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
            tags = version.get("metadata", {}).get("container", {}).get("tags", [])
            if sha in tags:
                match = version
                break
        if match is None:
            return None
        name = match.get("name", "")
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
    matching = [run for run in runs if run.get("headSha") == sha]
    if not matching:
        return "no publish run found"
    newest = max(matching, key=lambda run: run.get("databaseId", 0))
    status = newest.get("status")
    if status == "waiting":
        return "waiting for approval"
    if status in {"queued", "in_progress"}:
        return "in progress"
    return f"finished: {newest.get('conclusion') or status}"


def running_images(runner: Runner, env: Mapping[str, str]) -> dict[str, str | None]:
    """Digest ref each app container runs now, or None when it cannot be read."""
    running: dict[str, str | None] = {}
    for service in APP_SERVICES:
        running[service] = _running_image(runner, env, service)
    return running


def _running_image(runner: Runner, env: Mapping[str, str], service: str) -> str | None:
    inspect = runner.run(
        [*docker_base(), "inspect", "--format", "{{.Image}}", f"{PROJECT}-{service}-1"], env=env
    )
    image_id = inspect.stdout.strip()
    if inspect.returncode != 0 or not image_id:
        return None
    digests = runner.run(
        [*docker_base(), "image", "inspect", "--format", "{{json .RepoDigests}}", image_id],
        env=env,
    )
    if digests.returncode != 0:
        return None
    try:
        entries = json.loads(digests.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, str) and entry.startswith(f"{GHCR_OWNER}/"):
            return entry
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
    rollback: dict[str, str | None]
    pending: list[str]
    missing: list[str]
    checks: list[str]
    steps: list[str]


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
) -> Plan:
    sha = target_sha(runner, repo)
    images = published_images(runner, sha)
    if images is None:
        raise RolloutError(
            "plan", f"images for {sha[:7]} are not published yet ({publish_state(runner, sha)})"
        )
    flags = load_flags(repo / FLAGS_FILE)
    env = build_env(flags, images, docker_config=docker_config, secret_dir=secret_dir)
    rollback = running_images(runner, env)
    checkout = [version for version, _ in discover_migrations()]
    pending = pending_versions(checkout, applied_versions(runner, env))
    missing = missing_secrets(secret_paths(runner, env))
    checks = [path.name for path in check_files(checks_dir)]
    return Plan(sha, images, rollback, pending, missing, checks, plan_steps(pending, checks))


def format_plan(plan: Plan) -> str:
    lines = [f"Target SHA: {plan.sha}", "Images to deploy:"]
    lines += [f"  {app}: {plan.images[app]}" for app in APPS]
    lines.append("Rollback references (running now):")
    lines += [
        f"  {service}: {plan.rollback.get(service) or 'unknown (not running)'}"
        for service in APP_SERVICES
    ]
    lines.append(f"Pending migrations: {', '.join(plan.pending) if plan.pending else 'none'}")
    if plan.missing:
        lines += [
            f"Missing secret files: {', '.join(plan.missing)}",
            "apply will refuse until they exist",
        ]
    lines.append("Steps:")
    lines += [f"  {number}. {step}" for number, step in enumerate(plan.steps, start=1)]
    if plan.pending:
        lines.append(DOWNTIME)
    command = APPLY_COMMAND
    if plan.pending:
        command += f" --migrations {','.join(plan.pending)}"
    lines.append(command)
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts.staging_rollout", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="show what a rollout of HEAD would do (read-only)")
    apply = commands.add_parser("apply", help="roll HEAD out to staging")
    apply.add_argument("--migrations", help="comma-separated pending versions, as plan printed")
    return parser


def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "apply":
        print("Stopped at apply: apply is not implemented yet", file=sys.stderr)
        return 1
    runner = runner or SubprocessRunner(cwd=REPO_ROOT)
    docker_config = Path(tempfile.mkdtemp(prefix="csa-rollout-docker-"))
    try:
        docker_config.chmod(0o700)
        plan = build_plan(
            runner,
            REPO_ROOT,
            docker_config=docker_config,
            secret_dir=SECRET_DIR,
            checks_dir=CHECKS_DIR,
        )
    except RolloutError as error:
        print(f"Stopped at {error.step}: {error.message}")
        if error.state:
            print(error.state)
        return 1
    finally:
        shutil.rmtree(docker_config, ignore_errors=True)
    print(format_plan(plan))
    return 2 if plan.missing else 0


if __name__ == "__main__":
    sys.exit(main())
