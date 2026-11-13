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
import hashlib
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from scripts.migrate import MigrationError, discover_migrations
from scripts.rollout_targets import (
    BACKUP_SCRIPT,
    STAGING,
    TRAINING_KEY_SWITCH,
    TRAINING_KEY_VALUES,
    TRAINING_VIDEO_SWITCH,
    Target,
    get_target,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SOCKET = "unix:///var/run/docker.sock"
SECRET_DIR = STAGING.secret_dir
PROJECT = STAGING.project
APP_SERVICES = STAGING.app_services  # recreate order
SERVICE_APP = {
    "gateway": "python",
    "worker": "python",
    "delivery": "python",
    "frontend": "frontend",
    "training-worker": "python",
}
STOP_SERVICES = STAGING.stop_services
FLAGS_FILE = STAGING.flags_file
FLAG_KEYS = STAGING.required_keys
# Allowed in the flags file but not required, so a file without them still loads. Unset, compose
# uses the default written in the staging compose files. Each value must be one of its allowed
# values: a typo fails loudly here instead of reaching a service's environment.
DECIDER_MODES = ("auto", "llm", "shadow", "cascade")
OPTIONAL_FLAG_VALUES: dict[str, tuple[str, ...]] = {
    # Names the secret mapped to TYPESAFE_API_KEY (gateway and worker); unset it is the disabled
    # placeholder, so every Jev area runs on the LLM or rule. Only the two Jev secrets: any other
    # declared secret (a DSN, the Groq key) would be sent to the external Jev API as its token.
    "CSA_STAGING_JEV_SECRET": ("staging_disabled_jev_key", "staging_jev_key"),
    # The staff assistant's deciders (gateway ASSISTANT_DECIDER_*), unset auto. llm is the
    # per-area kill switch.
    "CSA_STAGING_ASSISTANT_DECIDER_EMAIL": DECIDER_MODES,
    "CSA_STAGING_ASSISTANT_DECIDER_ROUTING": DECIDER_MODES,
    "CSA_STAGING_ASSISTANT_DECIDER_INBOUND_INJECTION": DECIDER_MODES,
    # The customer classifier's mode for a company with no stored decide_mode (worker
    # CUSTOMER_DECIDE_MODE_DEFAULT), unset llm. The rollout flips it last.
    "CSA_STAGING_CUSTOMER_DECIDE_MODE_DEFAULT": ("llm", "cascade"),
}
# The staff deciders' Jev confidence floors per question type (gateway ASSISTANT_JEV_FLOOR_*),
# unset 0.7: a plain decimal from 0.5 to 1.0, at most three places.
FLOOR_FLAG_KEYS = frozenset({"CSA_STAGING_JEV_FLOOR_NOUL", "CSA_STAGING_JEV_FLOOR_CHOICE"})
FLOOR_RE = re.compile(r"0\.[5-9][0-9]{0,2}|1(?:\.0{1,3})?")  # whole value: is_floor
OPTIONAL_FLAG_KEYS = frozenset(OPTIONAL_FLAG_VALUES) | FLOOR_FLAG_KEYS


def is_floor(value: str) -> bool:
    """``value`` is a Jev floor switch value: ``FLOOR_RE`` over the whole value (fullmatch, as
    ``$`` would also accept a trailing newline)."""
    return FLOOR_RE.fullmatch(value) is not None


# Switch values that only mean something with the real Jev key: the gateway refuses to start with
# a decider at shadow or cascade and no key, and the customer cascade without a key is llm.
JEV_KEY_ON = ("CSA_STAGING_JEV_SECRET", "staging_jev_key")
NEEDS_JEV_KEY = frozenset({"shadow", "cascade"})
CHECKS_DIR = REPO_ROOT / "deploy" / "rollout_checks"
MIGRATION_APPROVAL = STAGING.migration_approval
READY_PROBE = (
    "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=5)"
)
READY_TRIES = 5
READY_PAUSE = 3
HEALTH_POLL = 5
HEALTH_WAIT = 300
STOPPED_NAMES = "gateway, frontend, worker, delivery and training-worker"
ALL_STOPPED = f"{STOPPED_NAMES} are stopped"
LIVE_CHECKOUT = STAGING.live_checkout
REVISION_LABEL = "org.opencontainers.image.revision"
# One JSON object per running image: its repo digests and its labels (for the revision).
RUNNING_FORMAT = '{"digests": {{json .RepoDigests}}, "labels": {{json .Config.Labels}} }'
LOCAL_ROLLBACK = "a local image with no registry digest, or not running"
PRODUCTION_RUNBOOK = "deploy/PRODUCTION.md"
READY_STATE = "services are up on the new images but /readyz is failing"
RERUN_HINT = "Re-run plan, then apply, once the cause is fixed."

GHCR_OWNER = STAGING.image_owner
APPS = ("python", "frontend")
IMAGE_ENV = {"python": "CSA_STAGING_PYTHON_IMAGE", "frontend": "CSA_STAGING_FRONTEND_IMAGE"}
PUBLISH_WORKFLOW = "publish-staging-images.yml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
HOST_CHANGED = "host settings changed since plan; run plan again"
MODULE_COMMAND = ".venv/bin/python -m scripts.staging_rollout"
APPLY_COMMAND = f"{MODULE_COMMAND} apply"
RECORD_COMMAND = f"{MODULE_COMMAND} record-staged"
TARGET_NAMES = ("staging", "production")
HOST_DIGEST_RE = re.compile(r"^[0-9a-f]{12}$")
GATE_ENVIRONMENT = "staging"
# The backup taken before anything changes (deploy/production/backup.sh, installed by path).
BACKUP_LOG = Path("/srv/csa-prod/backup/backup.log")
BACKUP_INSTALLED = Path(BACKUP_SCRIPT)
BACKUP_REPO_COPY = "deploy/production/backup.sh"
BACKUP_REINSTALL = f"install -m 0755 {BACKUP_REPO_COPY} {BACKUP_SCRIPT}"
BACKUP_TIMESTAMP = "%Y%m%dT%H%M%SZ"  # backup.sh: ts=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_OBJECT = "backups/csa_staging-{timestamp}.dump"  # backup.sh: s3://$B/backups/...-$ts.dump
BACKUP_LOG_TAIL = 4096  # bytes: the last line is all apply reads
BACKUP_FAILED = "The backup step failed; the deploy stopped before any change."
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


def load_flags(path: Path, target: Target = STAGING) -> dict[str, str]:
    """Read the non-secret switches: every required key once, each optional key at most once."""
    flags: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise RolloutError("flags", f"cannot read {path.name}") from error
    # The Jev switches are optional for every target; their values are checked below.
    known_keys = target.required_keys | target.optional_keys | OPTIONAL_FLAG_KEYS
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator:
            raise RolloutError("flags", f"{path.name} line {number} has no '='")
        if key not in known_keys:
            raise RolloutError("flags", f"{path.name} line {number}: unknown key {key}")
        if key in flags:
            raise RolloutError("flags", f"{path.name} line {number}: duplicate key {key}")
        value = value.strip()
        allowed = OPTIONAL_FLAG_VALUES.get(key) or target.optional_values.get(key)
        if allowed is not None and value not in allowed:
            # The value is not echoed: the file is not secret, but a bad line may still be a typo
            # of something that is.
            raise RolloutError(
                "flags", f"{path.name} line {number}: {key} must be one of {', '.join(allowed)}"
            )
        if key in FLOOR_FLAG_KEYS and not is_floor(value):
            raise RolloutError(
                "flags", f"{path.name} line {number}: {key} must be a decimal from 0.5 to 1.0"
            )
        flags[key] = value
    missing = sorted(target.required_keys - flags.keys())
    if missing:
        raise RolloutError("flags", f"{path.name} is missing: {', '.join(missing)}")
    if flags.get(JEV_KEY_ON[0]) != JEV_KEY_ON[1]:
        for key in sorted(OPTIONAL_FLAG_VALUES):
            if flags.get(key) in NEEDS_JEV_KEY:
                raise RolloutError(
                    "flags",
                    f"{path.name}: {key}={flags[key]} needs {JEV_KEY_ON[0]}={JEV_KEY_ON[1]}",
                )
    # Where the training worker holds an S3 key (production), video on with the placeholder key
    # would recreate the gateway with video on while the training worker refuses to start.
    real_key = TRAINING_KEY_VALUES[-1]
    if (
        TRAINING_KEY_SWITCH in target.optional_keys
        and flags.get(TRAINING_VIDEO_SWITCH) == "true"
        and flags.get(TRAINING_KEY_SWITCH) != real_key
    ):
        raise RolloutError(
            "flags",
            f"{path.name}: {TRAINING_VIDEO_SWITCH}=true needs {TRAINING_KEY_SWITCH}={real_key}",
        )
    return flags


def load_host_values(target: Target) -> dict[str, str]:
    """The instance's own settings: allow-listed keys only, each once. Values never printed."""
    if target.host_file is None:
        return {}
    name = target.host_file.name
    try:
        text = target.host_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise RolloutError("flags", f"cannot read {name}") from error
    allowed = target.host_required_keys | target.host_override_keys
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or line.startswith("export\t"):
            line = line[len("export") :].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator:
            raise RolloutError("flags", f"{name} line {number} has no '='")
        if key not in allowed:
            # A key that is not one of ours may be a pasted value (a secret before the "=" can
            # look like a name): print it only when it is a CSA_ name.
            shown = f" {key}" if KEY_RE.match(key) and key.startswith("CSA_") else ""
            raise RolloutError("flags", f"{name} line {number}: unknown key{shown}")
        if key in values:
            raise RolloutError("flags", f"{name} line {number}: duplicate key {key}")
        value = value.strip()
        # No shell here: quotes would reach compose as part of the value.
        if value[:1] in {'"', "'"}:
            raise RolloutError(
                "flags", f"{name} line {number}: quoted value; write KEY=value without quotes"
            )
        values[key] = value
    missing = sorted(target.host_required_keys - values.keys())
    if missing:
        raise RolloutError("flags", f"{name} is missing: {', '.join(missing)}")
    return values


def host_settings_digest(values: Mapping[str, str]) -> str:
    """Twelve hex of sha256 over the sorted ``KEY=VALUE`` lines: plan prints it, apply checks."""
    text = "".join(f"{key}={values[key]}\n" for key in sorted(values))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def docker_base() -> list[str]:
    return ["docker", "--host", SOCKET]


def container_names(target: Target, env: Mapping[str, str], service: str) -> list[str]:
    """Every container of ``service``: one, or the replica count the target allows env to set."""
    count = 1
    if service in target.replicas:
        variable, count = target.replicas[service]
        value = env.get(variable)
        if value is not None:
            if not value.isdecimal() or int(value) < 1:
                raise RolloutError("flags", f"{variable} must be a positive integer, got {value!r}")
            count = int(value)
    return [f"{target.project}-{service}-{number}" for number in range(1, count + 1)]


def restart_command(target: Target, env: Mapping[str, str]) -> str:
    """The command that starts every stopped container again on the images it had."""
    names = [
        name for service in target.stop_services for name in container_names(target, env, service)
    ]
    return " ".join([*docker_base(), "start", *names])


RESTART_COMMAND = restart_command(STAGING, {})


def compose_base(*, target: Target = STAGING) -> list[str]:
    files = [part for name in target.compose_files for part in ("--file", name)]
    profiles = [part for name in target.profiles for part in ("--profile", name)]
    return [
        *docker_base(),
        "compose",
        "--env-file",
        "/dev/null",
        "--project-name",
        target.project,
        *files,
        *profiles,
    ]


def build_env(
    flags: Mapping[str, str],
    images: Mapping[str, str],
    *,
    docker_config: Path,
    secret_dir: Path | None = None,
    target: Target = STAGING,
    host_values: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The whole environment for docker and compose; nothing else is inherited.

    Repo flags first, then host values: a host value replaces a repo flag only for the
    target's ``host_override_keys``.
    """
    host_values = host_values or {}
    clashing = sorted((host_values.keys() & flags.keys()) - target.host_override_keys)
    if clashing:
        raise RolloutError(
            "flags", f"set in both the repo file and the host file: {', '.join(clashing)}"
        )
    env = {name: os.environ[name] for name in ("PATH", "HOME") if name in os.environ}
    env |= {
        "NO_LOCAL_ENV": "1",
        "ENV": "ci",
        "COMPOSE_PROFILES": "",
        "CSA_STAGING_PROJECT": target.project,
        "CSA_STAGING_SECRET_DIR": str(target.secret_dir if secret_dir is None else secret_dir),
        "DOCKER_CONFIG": str(docker_config),
    }
    env |= {IMAGE_ENV[app]: images[app] for app in APPS}
    env |= flags
    env |= host_values
    return env


def _first_line(text: str) -> str:
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else ""


def target_sha(runner: Runner, repo: Path) -> str:
    """HEAD of a clean checkout that is already on origin/main. Never fetches."""
    head = runner.run(["git", "-C", str(repo), "rev-parse", "HEAD"])
    sha = head.stdout.strip()
    if head.returncode != 0 or not SHA_RE.fullmatch(sha):
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


def _gh(
    runner: Runner,
    argv: Sequence[str],
    step: str = "plan",
    *,
    body: Mapping[str, object] | None = None,
) -> str:
    """Run gh; a ``body`` is sent as JSON on stdin, for ``--input -``."""
    stdin = None if body is None else json.dumps(body)
    result = runner.run(argv, input=stdin)  # no env: gh keeps the user's login
    if result.returncode != 0:
        detail = _first_line(result.stderr) or f"exit {result.returncode}"
        raise RolloutError(step, f"gh failed: {detail}")
    return result.stdout


def _gh_json(
    runner: Runner,
    argv: Sequence[str],
    step: str = "plan",
    *,
    body: Mapping[str, object] | None = None,
) -> object:
    try:
        return json.loads(_gh(runner, argv, step, body=body))
    except json.JSONDecodeError as error:
        raise RolloutError(step, "gh returned unreadable output") from error


def _version_tags(version: object) -> list[object]:
    """The tags of one package version; any other shape is an unexpected listing."""
    metadata = version.get("metadata") if isinstance(version, dict) else None
    container = metadata.get("container") if isinstance(metadata, dict) else None
    tags = container.get("tags") if isinstance(container, dict) else None
    if not isinstance(tags, list):
        raise RolloutError("plan", "gh returned an unexpected package listing")
    return tags


def published_images(
    runner: Runner, sha: str, *, target: Target = STAGING
) -> dict[str, str] | None:
    """Digest refs for the version of each package tagged with the full SHA."""
    images: dict[str, str] = {}
    for app in APPS:
        versions = _gh_json(
            runner,
            [
                "gh",
                "api",
                f"/users/{target.package_owner}/packages/container/csa-{app}/versions?per_page=100",
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
        images[app] = f"{target.image_owner}/csa-{app}@{name}"
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


def staged_record(runner: Runner, target: Target, sha: str) -> str | None:
    """When ``sha`` first succeeded on staging, from GitHub Deployments; None if it never did.

    Fails closed: a gh failure or any answer not of the expected shape is a stop, never a pass.
    """
    if not SHA_RE.fullmatch(sha):
        raise RolloutError("gate", "not a full 40-character SHA")
    deployments = f"repos/{target.repo}/deployments"
    query = f"sha={sha}&environment={GATE_ENVIRONMENT}&per_page=100"
    listing = _gh_json(runner, ["gh", "api", f"{deployments}?{query}"], "gate")
    if not isinstance(listing, list):
        raise RolloutError("gate", "gh returned an unexpected deployment listing")
    succeeded: list[str] = []
    for deployment in listing:
        if not isinstance(deployment, dict):
            raise RolloutError("gate", "gh returned an unexpected deployment listing")
        number = deployment.get("id")
        if type(number) is not int:
            raise RolloutError("gate", "gh returned an unexpected deployment listing")
        if deployment.get("sha") != sha:
            raise RolloutError("gate", "gh returned a deployment for another commit")
        if deployment.get("environment", GATE_ENVIRONMENT) != GATE_ENVIRONMENT:
            raise RolloutError("gate", f"gh returned a deployment outside {GATE_ENVIRONMENT}")
        statuses = _gh_json(
            runner, ["gh", "api", f"{deployments}/{number}/statuses?per_page=100"], "gate"
        )
        if not isinstance(statuses, list) or not all(isinstance(s, dict) for s in statuses):
            raise RolloutError("gate", "gh returned an unexpected deployment status listing")
        for status in statuses:
            if status.get("state") != "success":
                continue
            created = status.get("created_at")
            if not isinstance(created, str) or not created:
                raise RolloutError("gate", "gh returned a deployment status without a date")
            succeeded.append(created)
    return min(succeeded) if succeeded else None


def require_staged(runner: Runner, target: Target, sha: str) -> str:
    """The staged record's date for a gated target; a commit staging never ran is refused."""
    if not target.staged_gate:
        return ""
    record = staged_record(runner, target, sha)
    if record is None:
        raise RolloutError("gate", f"commit {sha[:7]} has not been rolled out to staging")
    return record


def record_staged(runner: Runner, target: Target, sha: str) -> None:
    """Record ``sha`` as a successful staging deployment.

    ``auto_inactive`` false keeps an older commit's success after a newer one is recorded.
    """
    if not SHA_RE.fullmatch(sha):
        raise RolloutError("gate", "not a full 40-character SHA")
    deployments = f"repos/{target.repo}/deployments"
    created = _gh_json(
        runner,
        ["gh", "api", "--method", "POST", deployments, "--input", "-"],
        "gate",
        body={
            "ref": sha,
            "environment": GATE_ENVIRONMENT,
            "auto_merge": False,
            "required_contexts": [],
            "transient_environment": False,
            "production_environment": False,
            "description": "staging rollout",
        },
    )
    number = created.get("id") if isinstance(created, dict) else None
    if type(number) is not int:
        raise RolloutError("gate", "gh returned an unexpected deployment")
    _gh(
        runner,
        ["gh", "api", "--method", "POST", f"{deployments}/{number}/statuses", "--input", "-"],
        "gate",
        body={
            "state": "success",
            "environment": GATE_ENVIRONMENT,
            "auto_inactive": False,
            "description": "staging rollout finished",
        },
    )


@dataclass(frozen=True)
class RunningImage:
    """The image of an app container: its ghcr digest ref and the commit it was built from."""

    ref: str
    revision: str | None


def running_images(
    runner: Runner, env: Mapping[str, str], *, target: Target = STAGING
) -> dict[str, RunningImage | None]:
    """The image of each app container (running or stopped), or None when it cannot be read."""
    running: dict[str, RunningImage | None] = {}
    for service in target.app_services:
        running[service] = _running_image(runner, env, service, target=target)
    return running


def _running_image(
    runner: Runner, env: Mapping[str, str], service: str, *, target: Target = STAGING
) -> RunningImage | None:
    """The image of the service's first container: the rollback record reads replica 1."""
    return _container_image(runner, env, container_names(target, env, service)[0], target)


def _container_image(
    runner: Runner, env: Mapping[str, str], name: str, target: Target
) -> RunningImage | None:
    inspect = runner.run([*docker_base(), "inspect", "--format", "{{.Image}}", name], env=env)
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
    if not isinstance(revision, str) or not SHA_RE.fullmatch(revision):
        revision = None
    for entry in entries:
        if isinstance(entry, str) and entry.startswith(f"{target.image_owner}/"):
            return RunningImage(entry, revision)
    return None


def applied_versions(
    runner: Runner, env: Mapping[str, str], *, target: Target = STAGING
) -> list[str]:
    result = runner.run(
        [
            *compose_base(target=target),
            "exec",
            "-T",
            target.db_service,
            "psql",
            "-U",
            target.db_user,
            "-d",
            target.db_name,
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
            "plan",
            f"could not read schema_migrations from {target.name} (exit {result.returncode})",
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def pending_versions(
    checkout: Sequence[str], applied: Sequence[str], *, target: Target = STAGING
) -> list[str]:
    ahead = sorted(set(applied) - set(checkout))
    if ahead:
        raise RolloutError(
            "plan",
            f"{target.name} has migrations this checkout lacks: {', '.join(ahead)}; "
            "update the checkout before rolling out",
        )
    done = set(applied)
    return [version for version in checkout if version not in done]


def secret_paths(
    runner: Runner, env: Mapping[str, str], *, target: Target = STAGING
) -> dict[str, Path]:
    """Secret name to the file compose will mount, from the rendered config."""
    result = runner.run(
        [
            *compose_base(target=target),
            "--profile",
            "migration-approved",
            "config",
            "--format",
            "json",
        ],
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
    host_values: dict[str, str] = field(default_factory=dict, repr=False)  # values never printed
    warnings: list[str] = field(default_factory=list)
    staged_on: str = ""  # when staging first ran this commit; gated targets only


def plan_steps(
    pending: Sequence[str], checks: Sequence[str], *, target: Target = STAGING
) -> list[str]:
    steps = ["pull and verify images"]
    if pending:
        steps += [
            f"stop {', '.join(target.stop_services)}",
            f"apply migrations {', '.join(pending)}",
            "confirm migrations recorded",
        ]
    steps += [
        f"recreate {', '.join(target.app_services)}",
        "check readiness",
        f"run checks: {', '.join(checks) if checks else 'none'}",
    ]
    return steps


def downgrade_warnings(
    runner: Runner, repo: Path, sha: str, rollback: Mapping[str, RunningImage | None]
) -> list[str]:
    """Warn when what runs now was built from a newer commit than ``sha``; never stops a plan."""
    cannot_compare = f"WARNING: could not compare {sha[:7]} with the running revision"
    revisions = {image.revision if image else None for image in rollback.values()} - {sha}
    warnings: list[str] = []
    for revision in sorted(revisions, key=str):
        outcome = None
        if revision is not None:
            argv = ["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, revision]
            outcome = runner.run(argv).returncode
        if outcome == 0 and revision is not None:
            warnings.append(
                f"WARNING: {sha[:7]} is older than the running {revision[:7]}; "
                "this is a downgrade. A downgrade across a migration is refused."
            )
        elif outcome != 1 and cannot_compare not in warnings:
            warnings.append(cannot_compare)
    return warnings


def build_plan(
    runner: Runner,
    repo: Path,
    *,
    docker_config: Path,
    secret_dir: Path | None = None,
    checks_dir: Path = CHECKS_DIR,
    expected_sha: str | None = None,
    target: Target = STAGING,
    expected_host_digest: str | None = None,
) -> Plan:
    if expected_sha is not None and not SHA_RE.fullmatch(expected_sha):
        raise RolloutError("confirm", "--sha must be the full 40-character SHA that plan printed")
    sha = target_sha(runner, repo)
    if expected_sha is not None and expected_sha != sha:
        raise RolloutError(
            "confirm", f"HEAD changed since plan ({expected_sha[:7]} → {sha[:7]}); run plan again"
        )
    # Before any image lookup: a gated target takes only commits staging has run.
    staged_on = require_staged(runner, target, sha)
    images = published_images(runner, sha, target=target)
    if images is None:
        raise RolloutError(
            "plan", f"images for {sha[:7]} are not published yet ({publish_state(runner, sha)})"
        )
    flags = load_flags(repo / target.flags_file, target=target)
    host_values = load_host_values(target)
    # Before any docker command: replica counts and tuning must be what plan printed.
    if expected_host_digest is not None and expected_host_digest != host_settings_digest(
        host_values
    ):
        raise RolloutError("confirm", HOST_CHANGED)
    env = build_env(
        flags,
        images,
        docker_config=docker_config,
        secret_dir=secret_dir,
        target=target,
        host_values=host_values,
    )
    # Secrets first: a host with a missing file lists it instead of failing on a staging read.
    missing = missing_secrets(secret_paths(runner, env, target=target))
    rollback = running_images(runner, env, target=target)
    # Migrations come from this module's checkout, so main always passes REPO_ROOT.
    try:
        checkout = [version for version, _ in discover_migrations()]
    except MigrationError as error:
        raise RolloutError("plan", str(error)) from error
    pending = pending_versions(
        checkout, applied_versions(runner, env, target=target), target=target
    )
    warnings = downgrade_warnings(runner, repo, sha, rollback) if target.downgrade_warning else []
    checks = [path.name for path in check_files(checks_dir)]
    steps = plan_steps(pending, checks, target=target)
    return Plan(
        sha,
        images,
        rollback,
        pending,
        missing,
        checks,
        steps,
        env,
        host_values,
        warnings,
        staged_on,
    )


def _pending_line(pending: Sequence[str]) -> str:
    return f"Pending migrations: {', '.join(pending) if pending else 'none'}"


def format_plan(plan: Plan, *, target: Target = STAGING) -> str:
    lines = [f"Target SHA: {plan.sha}"]
    if target.staged_gate:
        lines.append(f"Staged on: {plan.staged_on}")
    lines.append("Images to deploy:")
    lines += [f"  {app}: {plan.images[app]}" for app in APPS]
    lines += plan.warnings
    lines.append("Rollback references (current containers):")
    unknown = f"unknown ({LOCAL_ROLLBACK})" if target.name != "staging" else "unknown (not running)"
    for service in target.app_services:
        current = plan.rollback.get(service)
        lines.append(f"  {service}: {current.ref if current else unknown}")
    lines.append(_pending_line(plan.pending))
    if plan.missing:
        lines += [
            f"Missing secret files: {', '.join(plan.missing)}",
            "apply will refuse until they exist",
        ]
    if target.host_file is not None:
        overrides = sorted(plan.host_values.keys() & target.host_override_keys)
        lines.append(f"Host overrides: {', '.join(overrides) if overrides else 'none'}")
        lines.append(f"Host settings digest: {host_settings_digest(plan.host_values)}")
    lines.append("Steps:")
    lines += [f"  {number}. {step}" for number, step in enumerate(plan.steps, start=1)]
    if plan.pending:
        lines.append(DOWNTIME)
    command = APPLY_COMMAND
    if target.name != "staging":
        command = f"{MODULE_COMMAND} --target {target.name} apply"
    command += f" --sha {plan.sha}"
    if plan.pending:
        command += f" --migrations {','.join(plan.pending)}"
    if target.host_file is not None:
        command += f" --host-digest {host_settings_digest(plan.host_values)}"
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


@dataclass(frozen=True)
class BackupResult:
    timestamp: str  # as logged
    object_name: str  # "backups/csa_staging-<timestamp>.dump"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _last_log_line(path: Path) -> str:
    """The last non-empty line of the backup log, reading only its tail."""
    try:
        with path.open("rb") as log:
            size = log.seek(0, os.SEEK_END)
            log.seek(max(0, size - BACKUP_LOG_TAIL))
            tail = log.read()
    except OSError as error:
        raise RolloutError("backup", f"cannot read {path} after the backup ran") from error
    lines = [line.strip() for line in tail.decode("utf-8", "replace").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        raise RolloutError("backup", f"{path} is empty after the backup ran")
    return lines[-1]


def run_pre_change(
    runner: Runner,
    target: Target,
    repo: Path,
    *,
    now: Callable[[], datetime] = _utc_now,
    log_path: Path = BACKUP_LOG,
    installed: Path = BACKUP_INSTALLED,
) -> BackupResult | None:
    """Take the backup ``target.pre_change`` names and prove it ran now; None when there is none.

    A backup counts only when the script exits 0 and the log's last line is ``<ts> ok ...``
    stamped no earlier than this call: a lock-held run exits 0 with a ``skipped`` line instead.
    The script's own output is not printed: an aws error can carry the bucket name.
    """
    if target.pre_change is None:
        return None
    argv = list(target.pre_change)
    if not argv or any(Path(part).name == "sudo" for part in argv):
        raise RolloutError("backup", "the backup command must be a plain argv without sudo")
    try:
        expected = (repo / BACKUP_REPO_COPY).read_bytes()
    except OSError as error:
        raise RolloutError("backup", f"cannot read {BACKUP_REPO_COPY} in the checkout") from error
    try:
        current: bytes | None = installed.read_bytes()
    except OSError:
        current = None
    if current != expected:
        why = "is missing" if current is None else f"differs from {BACKUP_REPO_COPY}"
        raise RolloutError("backup", f"{installed} {why}; reinstall it: {BACKUP_REINSTALL}")
    started = now()
    if started.tzinfo is None:
        raise ValueError("now() must return an aware datetime")
    # backup.sh stamps its line at whole seconds when it starts, so compare at whole seconds.
    start = started.astimezone(UTC).replace(microsecond=0)
    result = runner.run(argv)
    if result.returncode != 0:
        raise RolloutError(
            "backup",
            f"{argv[0]} failed (exit {result.returncode}); run it on the instance to see why",
        )
    line = _last_log_line(log_path)
    fields = line.split()
    try:
        stamped = datetime.strptime(fields[0], BACKUP_TIMESTAMP).replace(tzinfo=UTC)
    except (IndexError, ValueError) as error:
        raise RolloutError(
            "backup", f"the backup log's last line has no timestamp: {line}"
        ) from error
    if fields[1:2] != ["ok"]:
        raise RolloutError("backup", f"the backup did not report ok: {line}")
    if stamped < start:
        raise RolloutError("backup", f"the backup log's last ok line is from before now: {line}")
    return BackupResult(fields[0], BACKUP_OBJECT.format(timestamp=fields[0]))


@dataclass
class _Progress:
    """How far apply got, so a stop can say what changed and how to recover."""

    phase: str = "unchanged"  # unchanged, backup, stopped, recreating, up, checks or check_unread
    recorded: list[str] | None = field(default_factory=list)  # planned versions recorded
    pending: list[str] = field(default_factory=list)
    rollback: Mapping[str, RunningImage | None] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)  # names the replicas to restart
    backup: str | None = None  # the object name of the backup taken before any change


def _join(names: Sequence[str]) -> str:
    """``["a", "b", "c"]`` -> ``"a, b and c"``."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _recorded(
    runner: Runner, env: Mapping[str, str], pending: Sequence[str], *, target: Target = STAGING
) -> tuple[list[str] | None, str]:
    """The planned versions schema_migrations records now, or None with why it is unknown."""
    try:
        applied = applied_versions(runner, env, target=target)
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
    *,
    target: Target = STAGING,
) -> None:
    out(f"Stopping {', '.join(target.stop_services)}…")
    progress.phase = "stopped"  # a failed stop may still have stopped some of them
    _run_step(
        runner,
        [*compose_base(target=target), "stop", *target.stop_services],
        env,
        "stop",
        "compose stop",
        f"some of {STOPPED_NAMES} may still be running; check compose ps",
    )
    out(f"Applying migrations {', '.join(pending)}…")
    approval, approved = target.migration_approval
    migrate_env = {**env, approval: approved}
    result = runner.run(
        [
            *compose_base(target=target),
            "--profile",
            "migration-approved",
            "run",
            "--rm",
            "--no-deps",
            "migrate",
        ],
        env=migrate_env,
    )
    if result.stdout.strip():
        out(result.stdout.rstrip())
    out(f"migrate exit={result.returncode}")
    if result.returncode != 0:
        # Read what migrate left behind: it decides between restarting and fixing forward.
        progress.recorded, _ = _recorded(runner, env, pending, target=target)
        raise RolloutError(
            "migrate",
            f"migrate exited {result.returncode}",
            _migrate_state(pending, progress.recorded),
        )
    out("Confirming migrations recorded…")
    progress.recorded, why = _recorded(runner, env, pending, target=target)
    state = _migrate_state(pending, progress.recorded)
    if progress.recorded is None:
        raise RolloutError("migrate", why, state)
    absent = [version for version in pending if version not in progress.recorded]
    if absent:
        raise RolloutError(
            "migrate", f"schema_migrations lacks {', '.join(absent)} after migrate", state
        )


def _recreate_state(service: str, stopped: bool, *, target: Target = STAGING) -> str:
    """What is down when ``service`` failed to come up on its new image."""
    services = target.app_services
    index = services.index(service)
    done, later = services[:index], services[index + 1 :]
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
    target: Target = STAGING,
) -> None:
    state = _recreate_state(service, stopped, target=target)
    up = runner.run(
        [
            *compose_base(target=target),
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
    # A replicated service counts only when every replica is healthy on the planned image.
    out(f"{service} reported unhealthy; waiting for it to become healthy")
    names = container_names(target, env, service)
    waited = 0
    while True:
        if all(
            _on_new_image(runner, env, name, expected, service, state, target) for name in names
        ):
            return
        if waited >= HEALTH_WAIT:
            raise RolloutError(
                f"recreate {service}",
                f"{service} was not healthy on the new image within {HEALTH_WAIT} s",
                state,
            )
        sleep(HEALTH_POLL)
        waited += HEALTH_POLL


def _on_new_image(
    runner: Runner,
    env: Mapping[str, str],
    name: str,
    expected: str,
    service: str,
    state: str,
    target: Target,
) -> bool:
    """Whether container ``name`` is healthy and runs ``expected``; a missing one is a stop."""
    health = runner.run(
        [*docker_base(), "inspect", "--format", "{{.State.Health.Status}}", name], env=env
    )
    if health.returncode != 0:
        raise RolloutError(
            f"recreate {service}", f"{service} container not found after up failed", state
        )
    if health.stdout.strip() != "healthy":
        return False
    current = _container_image(runner, env, name, target)
    return current is not None and current.ref == expected


def _await_ready(
    runner: Runner,
    env: Mapping[str, str],
    sleep: Callable[[float], None],
    *,
    target: Target = STAGING,
) -> None:
    gateway = container_names(target, env, "gateway")[0]
    argv = [*docker_base(), "exec", gateway, "python", "-c", READY_PROBE]
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
    *,
    target: Target = STAGING,
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
                *compose_base(target=target),
                "exec",
                "-T",
                target.db_service,
                "psql",
                "-U",
                target.db_user,
                "-d",
                target.db_name,
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
        stopped = runner.run([*compose_base(target=target), "stop", *target.stop_services], env=env)
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
    rollback: Mapping[str, RunningImage | None],
    out: Callable[[str], None],
    *,
    target: Target = STAGING,
) -> None:
    """Image-only rollouts: the images to go back to and the commit that publishes them."""
    out("Rollback images:")
    known = [(service, image) for service, image in rollback.items() if image]
    if not known and target.name != "staging":
        # Before the first deploy from the registry the running images are local tags.
        out(f"  none known ({LOCAL_ROLLBACK})")
        out(
            "The previous images are local, so plan and apply cannot bring them back; "
            f"follow the manual rollback runbook in {PRODUCTION_RUNBOOK}."
        )
        return
    if not known:
        out("  none known (not running)")
        return
    for service, image in known:
        out(f"  {service}: {image.ref} (revision {image.revision or 'unknown'})")
    revisions = {image.revision for _, image in known}
    if len(revisions) == 1 and None not in revisions:
        out(
            f"To roll back: git checkout {revisions.pop()} in {target.live_checkout}, run plan and "
            "apply, then git checkout main."
        )
    else:
        out(
            "The rollback images do not share one known revision: pick one, git checkout it in "
            f"{target.live_checkout}, run plan and apply, then git checkout main."
        )


def _report_backup(progress: _Progress, out: Callable[[str], None]) -> None:
    if progress.backup is not None:
        out(
            f"The backup taken at the start of this deploy is {progress.backup} "
            f"(restore: {PRODUCTION_RUNBOOK})"
        )


def _report_recovery(
    progress: _Progress, out: Callable[[str], None], *, target: Target = STAGING
) -> None:
    """Recovery lines for how far apply got; never a rollback once a migration is recorded."""
    if progress.phase == "unchanged":
        out(f"Nothing on {target.name} was changed.")
        return
    if progress.phase == "backup":
        out(f"Nothing on {target.name} was changed. {BACKUP_FAILED}")
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
        out(restart_command(target, progress.env))
        out("If any is recorded, do not roll back; fix forward, then re-run plan and apply.")
        _report_backup(progress, out)
        return
    if progress.recorded:
        out(
            f"Migrations were applied: {', '.join(progress.recorded)}. The previous images fail "
            "/readyz against this schema, so do not roll back; fix forward, then re-run plan "
            "and apply."
        )
        _report_backup(progress, out)
        return
    if progress.phase == "stopped":
        out("To bring the previous version back:")
        out(restart_command(target, progress.env))
        return
    _report_rollback(progress.rollback, out, target=target)


def _print_target(plan: Plan, out: Callable[[str], None], *, target: Target = STAGING) -> None:
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
    secret_dir: Path | None = None,
    checks_dir: Path = CHECKS_DIR,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
    target: Target = STAGING,
    host_digest: str | None = None,
    now: Callable[[], datetime] = _utc_now,
    backup_log: Path = BACKUP_LOG,
    backup_installed: Path = BACKUP_INSTALLED,
) -> int:
    progress = _Progress()
    try:
        if target.host_file is not None and host_digest is None:
            raise RolloutError("confirm", HOST_CHANGED)
        plan = build_plan(
            runner,
            repo,
            docker_config=docker_config,
            secret_dir=secret_dir,
            checks_dir=checks_dir,
            expected_sha=sha,
            target=target,
            expected_host_digest=host_digest,
        )
        env = plan.env
        progress.pending = plan.pending
        progress.rollback = plan.rollback
        progress.env = env
        if confirmed != plan.pending:
            raise RolloutError("confirm", "pending migrations changed since plan; run plan again")
        if plan.missing:
            raise RolloutError("preflight", f"missing secret files: {', '.join(plan.missing)}")
        _print_target(plan, out, target=target)
        out("Checking compose config…")
        _run_step(
            runner,
            [*compose_base(target=target), "config", "--quiet"],
            env,
            "config",
            "compose config --quiet",
        )
        if target.pre_change is not None:
            out("Taking a backup…")
            progress.phase = "backup"
            backup = run_pre_change(
                runner, target, repo, now=now, log_path=backup_log, installed=backup_installed
            )
            progress.phase = "unchanged"
            if backup is not None:
                progress.backup = backup.object_name
                out(f"Backup taken: {backup.object_name}")
        out("Pulling and verifying images…")
        for app in APPS:
            _verify_pull(runner, env, plan.images[app], plan.sha)
        if plan.pending:
            _migrate(runner, env, plan.pending, out, progress, target=target)
        progress.phase = "recreating"
        for service in target.app_services:
            out(f"Recreating {service}…")
            expected = plan.images[SERVICE_APP[service]]
            _recreate(
                runner,
                env,
                service,
                expected,
                sleep,
                out,
                stopped=bool(plan.pending),
                target=target,
            )
        progress.phase = "up"
        out("Checking readiness…")
        _await_ready(runner, env, sleep, target=target)
        progress.phase = "checks"
        _run_checks(runner, env, checks_dir, out, progress, target=target)
        status = runner.run(
            [*compose_base(target=target), "ps", "--format", "{{.Service}} {{.Status}}"], env=env
        )
        if status.returncode == 0:
            out(status.stdout.rstrip())
        else:
            out(f"could not list services (compose ps exit {status.returncode})")
        out(f"Rollout of {plan.sha[:7]} finished.")
        return 0
    except RolloutError as error:
        _report_stop(error, out)
        _report_recovery(progress, out, target=target)
        out(RERUN_HINT)
        return 1


def record_staged_command(
    runner: Runner,
    target: Target,
    sha: str,
    out: Callable[[str], None] = print,
    *,
    repo: Path | None = None,
) -> int:
    """``record-staged``: write the staging record again after a failed write.

    The sha is the operator's, so it must be on origin/main in the live checkout (as the
    rollout it records was): a typo or another branch's commit is never recorded as staged.
    """
    repo = REPO_ROOT if repo is None else repo
    try:
        if target.name != GATE_ENVIRONMENT:
            raise RolloutError(
                "gate", f"record-staged records staging rollouts only, not {target.name}"
            )
        if not SHA_RE.fullmatch(sha):
            raise RolloutError("gate", "--sha must be the full 40-character SHA")
        argv = ["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, "origin/main"]
        outcome = runner.run(argv).returncode
        if outcome == 1:
            raise RolloutError(
                "gate",
                f"commit {sha[:7]} is not on origin/main; "
                "record-staged records only commits on main",
            )
        if outcome != 0:
            raise RolloutError(
                "gate", f"could not check that {sha[:7]} is on origin/main (git exit {outcome})"
            )
        record_staged(runner, target, sha)
    except RolloutError as error:
        _report_stop(error, out)
        return 1
    out(f"Recorded {sha[:7]} as staged")
    return 0


def _record_rollout(runner: Runner, sha: str, out: Callable[[str], None]) -> None:
    """After a successful staging apply; staging stands even when the record is not written."""
    try:
        record_staged(runner, STAGING, sha)
    except RolloutError as error:
        out(f"WARNING: staging is fine but the staged record was not written: {error.message}")
        out(f"Retry: {RECORD_COMMAND} --sha {sha}")
        return
    out(f"Recorded {sha[:7]} as staged")


def _host_digest(value: str) -> str:
    if not HOST_DIGEST_RE.match(value):
        raise argparse.ArgumentTypeError("must be 12 lowercase hex characters, as plan printed")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts.staging_rollout", description=__doc__)
    parser.add_argument(
        "--target", choices=TARGET_NAMES, default="staging", help="where to roll out"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="show what a rollout of HEAD would do (read-only)")
    apply = commands.add_parser("apply", help="roll HEAD out to the target")
    apply.add_argument("--sha", required=True, help="the full target SHA, as plan printed")
    apply.add_argument("--migrations", help="comma-separated pending versions, as plan printed")
    apply.add_argument(
        "--host-digest",
        type=_host_digest,
        help="the host settings digest, as plan printed (targets with a host file)",
    )
    record = commands.add_parser(
        "record-staged", help="record a staging rollout for production (retry after a failed write)"
    )
    record.add_argument("--sha", required=True, help="the full SHA of the staging rollout")
    return parser


def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    target = get_target(args.target)
    if args.command == "apply":
        if target.host_file is not None and args.host_digest is None:
            parser.error(f"apply --target {target.name} requires --host-digest, as plan printed")
        if target.host_file is None and args.host_digest is not None:
            parser.error(f"--host-digest is not used by --target {target.name}")
    runner = runner or SubprocessRunner(cwd=REPO_ROOT)
    if args.command == "record-staged":
        return record_staged_command(runner, target, args.sha, repo=REPO_ROOT)
    # Staging keeps its module-level directory (tests point it elsewhere).
    secret_dir = SECRET_DIR if target.name == "staging" else target.secret_dir
    docker_config = Path(tempfile.mkdtemp(prefix="csa-rollout-docker-"))
    try:
        docker_config.chmod(0o700)
        if args.command == "apply":
            code = apply(
                runner,
                REPO_ROOT,
                parse_migrations(args.migrations),
                sha=args.sha,
                docker_config=docker_config,
                secret_dir=secret_dir,
                checks_dir=CHECKS_DIR,
                target=target,
                host_digest=args.host_digest,
            )
            if code == 0 and target.name == GATE_ENVIRONMENT:
                # Not inside apply: a failed record must not turn a good rollout into a stop.
                # Only a staging rollout is a staged record; no other target writes one.
                _record_rollout(runner, args.sha, print)
            return code
        plan = build_plan(
            runner,
            REPO_ROOT,
            docker_config=docker_config,
            secret_dir=secret_dir,
            checks_dir=CHECKS_DIR,
            target=target,
        )
    except RolloutError as error:
        _report_stop(error, print)
        return 1
    finally:
        shutil.rmtree(docker_config, ignore_errors=True)
    print(format_plan(plan, target=target))
    return 2 if plan.missing else 0


if __name__ == "__main__":
    sys.exit(main())
