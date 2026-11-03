"""Reset staging with real accounts: ``plan``, then ``wipe``, then ``accounts``.

``plan`` re-derives everything ``wipe`` will refuse on (a clean checkout on origin/main, every
service running the image built from HEAD, a valid roster, every secret file present, enough
disk for the backup) and prints what is about to be lost. It runs only read commands: git,
``docker inspect``, ``compose config``, ``psql`` with ``SELECT``, and one-off containers with no
network and read-only mounts that count the files in each upload volume and the folders user
1000 does not own, since a wipe could not empty those.

``wipe`` and ``accounts`` are for the owner at a terminal; they refuse anywhere else. ``wipe``
backs staging up, drops and rebuilds the database, empties the upload volumes and creates the
first platform admin. It stays safe to repeat after a failure, and a repeat takes a new backup.
``accounts`` adds the other platform admins and runs ``scripts.staging_accounts`` inside the
gateway, with the first admin's password and the roster on stdin, never in argv or the
environment.

The targets are constants. Nothing here takes the project, the database, the host or a volume
from an argument or the environment. ``plan`` never prints the roster's addresses.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from scripts.migrate import MigrationError, discover_migrations
from scripts.staging_rollout import (
    APP_SERVICES,
    FLAGS_FILE,
    IMAGE_ENV,
    MIGRATION_APPROVAL,
    PROJECT,
    REPO_ROOT,
    RESTART_COMMAND,
    SERVICE_APP,
    STOP_SERVICES,
    Completed,
    RolloutError,
    Runner,
    RunningImage,
    SubprocessRunner,
    _await_ready,
    _recreate,
    _report_stop,
    build_env,
    compose_base,
    docker_base,
    load_flags,
    missing_secrets,
    running_images,
    secret_paths,
    target_sha,
)
from scripts.staging_roster import ROSTER_PATH, Roster, RosterError, load_roster

DATABASE = "csa_staging"
UPLOAD_VOLUMES = ("csa-staging_staging-uploads", "csa-staging_staging-hr-uploads")
CONFIRM_PHRASE = "wipe csa-staging"
DROP_DATABASE_SQL = "DROP DATABASE IF EXISTS csa_staging WITH (FORCE)"
CREATE_DATABASE_SQL = "CREATE DATABASE csa_staging OWNER csa"
DATABASE_COUNT_SQL = "SELECT count(*) FROM pg_database WHERE datname = 'csa_staging'"
BACKUP_ROOT = Path.home() / "csa-staging-backups"
DISK_MARGIN = 1 << 30  # kept free beyond the backup itself
MAINTENANCE_DATABASE = "postgres"
# The tables the plan asks about before it reads from them: the base schema's ``clients``, the
# identity table from migration 0270, and the migration ledger.
TABLE_PROBE = (
    "SELECT to_regclass('public.clients') IS NOT NULL, "
    "to_regclass('public.console_users') IS NOT NULL, "
    "to_regclass('public.schema_migrations') IS NOT NULL"
)
# One-off containers read an upload volume with no network, no secrets and a read-only mount.
# Files; folders user 1000 does not own, starting at /data so the mount itself counts; bytes. du
# is last because the script exits with its last command's code, and a pipe into wc hides failure.
VOLUME_SCRIPT = "find /data -type f | wc -l; find /data -type d ! -user 1000 | wc -l; du -sb /data"
VOLUME_OUTPUT = re.compile(r"([0-9]+)\n([0-9]+)\n([0-9]+)\t/data\n")
LOST = (
    "every account and session",
    "all HR and customer data",
    "knowledge base files",
    "Telegram, WhatsApp and Gmail connections",
)
WIPE_COMMAND = ".venv/bin/python -m scripts.staging_reset wipe"
ACCOUNTS_COMMAND = ".venv/bin/python -m scripts.staging_reset accounts"
ROLLOUT_PLAN_COMMAND = ".venv/bin/python -m scripts.staging_rollout plan"
PLACEHOLDER_IMAGE = "unused-until-the-running-images-are-read"
PG_DUMP_COMMAND = ("pg_dump", "-U", "csa", "-d", DATABASE, "-Fc")
DUMP_FILE = f"{DATABASE}.dump"
MANIFEST_FILE = "manifest.json"
INCOMPLETE = "(incomplete, do not restore)"
STDERR_TAIL_LINES = 5
MIGRATION_ERROR_PREFIX = "migration error:"  # scripts/migrate.py; its texts name no connection
BOOTSTRAP_APPROVAL = ("CSA_STAGING_ADMIN_BOOTSTRAP_APPROVAL", "approved-isolated-staging")
BOOTSTRAP_TRIES = 3  # safe to repeat: bootstrap closes for good once one admin exists
ADMIN_ADD_APPROVAL = ("CSA_STAGING_ADMIN_ADD_APPROVAL", "approved-isolated-staging")
GATEWAY_CONTAINER = f"{PROJECT}-gateway-1"
# One-off containers: no network, nothing writable but a mount, no secrets, no capabilities.
SEALED_RUN = (
    "run",
    "--rm",
    "--network",
    "none",
    "--read-only",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges:true",
)
BASELINE_IN_IMAGE = "/app/db/schema_baseline.sql"
EMPTY_VOLUME_SCRIPT = "find /data -mindepth 1 -delete"
# As the first-install procedure asks in deploy/RELEASE.md, before it loads the base schema.
NO_TABLES_SQL = (
    "SELECT to_regclass('public.schema_migrations') IS NULL "
    "AND to_regclass('public.contacts') IS NULL"
)
UNCHANGED = "Nothing on staging was changed."
SERVICES_STOPPED = "services are stopped"
UPLOADS_EMPTY = "the upload volumes are empty"
NOT_DROPPED = f"{SERVICES_STOPPED}; {DATABASE} was not dropped; the upload volumes are as they were"
DROPPED = f"{SERVICES_STOPPED}; {DATABASE} does not exist; the upload volumes are as they were"
MAYBE_DROPPED = (
    f"{SERVICES_STOPPED}; {DATABASE} may not exist any more; the upload volumes are as they were"
)
INTACT = "Nothing is lost. Fix the cause and run wipe again, or bring staging back with:"
RECOVERY = (
    'Run wipe again, or restore from the backup (deploy/STAGING_PIPELINE.md, "Resetting staging").'
)
MOVED_HEAD = (
    "If HEAD has moved since this wipe started, restore the backup and roll out before "
    "wiping again."
)


class ResetRunner(Runner, Protocol):
    """A ``Runner`` that can also hand the terminal to a child and stream stdout to a file."""

    def run_tty(self, argv: Sequence[str], *, env: Mapping[str, str] | None) -> int:
        """Run with stdin, stdout and stderr inherited; return the exit code."""
        ...

    def run_to_file(self, argv: Sequence[str], *, env: Mapping[str, str] | None, path: Path) -> int:
        """Write stdout as bytes to a new file with mode 0600; return the exit code."""
        ...


class SubprocessResetRunner(SubprocessRunner):
    """The real runner: ``cwd`` is the repo, so relative compose file paths resolve."""

    def run_tty(self, argv: Sequence[str], *, env: Mapping[str, str] | None) -> int:
        """Run with stdin, stdout and stderr inherited, so the child can prompt."""
        return self._call(argv, env=env, stdout=None)

    def run_to_file(self, argv: Sequence[str], *, env: Mapping[str, str] | None, path: Path) -> int:
        """Write the child's stdout as bytes to a new file (mode 0600); never overwrite one."""
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as error:
            raise RolloutError(
                "backup", f"could not create {path.name}: {error.strerror}"
            ) from error
        with os.fdopen(descriptor, "wb") as handle:
            return self._call(argv, env=env, stdout=handle)

    def _call(self, argv: Sequence[str], *, env: Mapping[str, str] | None, stdout: object) -> int:
        binary = argv[0]
        try:
            return subprocess.run(
                list(argv),
                env=env,
                stdout=stdout,
                check=False,
                cwd=self.cwd,
            ).returncode
        except FileNotFoundError as error:
            raise RolloutError(binary, f"{binary} not found") from error
        except OSError as error:
            raise RolloutError(binary, f"could not run {binary}: {error.strerror}") from error


@dataclass(frozen=True)
class StagingState:
    """What staging holds now: counts and slugs only, never content."""

    database_exists: bool
    has_schema: bool
    slugs: tuple[str, ...]
    accounts: int
    database_bytes: int
    upload_files: dict[str, int]
    upload_bytes: dict[str, int]
    schema_version: str | None = None  # newest recorded migration, when the ledger has one


@dataclass(frozen=True)
class ResetContext:
    """What ``plan`` and ``wipe`` both work from, once every refusal has passed."""

    sha: str
    env: dict[str, str] = field(repr=False)  # non-secret flags and image refs only
    python_image: str
    roster: Roster = field(repr=False)  # holds addresses
    state: StagingState
    newest_migration: str = ""  # what the rebuild will migrate up to
    free_bytes: int = 0  # free disk on the backup's filesystem when prepare ran


def _home_free() -> int:
    return shutil.disk_usage(Path.home()).free


def _stderr_tail(stderr: str) -> str:
    """The last lines of a step's stderr, for steps that handle no secret; "" when it has none."""
    lines = [line for line in stderr.splitlines() if line.strip()]
    return "\n".join(lines[-STDERR_TAIL_LINES:])


def _with_reason(message: str, stderr: str) -> str:
    tail = _stderr_tail(stderr)
    return f"{message}\n{tail}" if tail else message


def _psql_argv(database: str, *arguments: str) -> list[str]:
    return [
        *compose_base(),
        "exec",
        "-T",
        "staging-db",
        "psql",
        "-U",
        "csa",
        "-d",
        database,
        *arguments,
    ]


def _psql(runner: Runner, env: Mapping[str, str], database: str, sql: str) -> str:
    result = runner.run(
        _psql_argv(database, "-tA", "-v", "ON_ERROR_STOP=1", "-c", sql),
        env=env,
    )
    if result.returncode != 0:
        raise RolloutError(
            "state",
            _with_reason(
                f"could not read staging with psql on {database} (exit {result.returncode})",
                result.stderr,
            ),
        )
    return result.stdout.strip()


def _integer(text: str, what: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise RolloutError("state", f"staging returned an unreadable {what}") from None


def _volume_run(image: str, mount: str, entrypoint: str, *arguments: str) -> list[str]:
    """A sealed one-off container on one upload volume, as the UID the services write it with."""
    return [
        *docker_base(),
        *SEALED_RUN,
        "--user",
        "1000:1000",
        "-v",
        mount,
        "--entrypoint",
        entrypoint,
        image,
        *arguments,
    ]


def _volume_usage(
    runner: Runner, env: Mapping[str, str], python_image: str, volume: str
) -> tuple[int, int]:
    """File count and bytes of one upload volume, read through a throwaway container."""
    # ``docker run -v name:`` creates a missing volume, so look first.
    exists = runner.run([*docker_base(), "volume", "inspect", volume], env=env)
    if exists.returncode != 0:
        raise RolloutError("state", f"volume {volume} does not exist")
    result = runner.run(
        _volume_run(python_image, f"{volume}:/data:ro", "sh", "-c", VOLUME_SCRIPT), env=env
    )
    found = VOLUME_OUTPUT.fullmatch(result.stdout)
    if result.returncode != 0 or found is None:
        raise RolloutError("state", f"could not read volume {volume} (exit {result.returncode})")
    files, foreign_folders, size = (int(number) for number in found.groups())
    if foreign_folders:
        raise RolloutError(
            "state",
            f"volume {volume} has {foreign_folders} folder(s) not owned by user 1000, so the "
            "wipe could not empty it. Give them to user 1000 first.",
        )
    return files, size


def read_state(runner: Runner, env: Mapping[str, str], python_image: str) -> StagingState:
    """Read what staging holds. A missing database or a database with no schema is a state."""
    files: dict[str, int] = {}
    sizes: dict[str, int] = {}
    for volume in UPLOAD_VOLUMES:
        files[volume], sizes[volume] = _volume_usage(runner, env, python_image, volume)
    size = _psql(
        runner,
        env,
        MAINTENANCE_DATABASE,
        f"SELECT pg_database_size(datname) FROM pg_database WHERE datname = '{DATABASE}'",
    )
    if not size:
        return StagingState(False, False, (), 0, 0, files, sizes)
    database_bytes = _integer(size, "database size")
    flags = _psql(runner, env, DATABASE, TABLE_PROBE).split("|")
    if len(flags) != 3 or any(flag not in {"t", "f"} for flag in flags):
        raise RolloutError("state", "staging returned an unreadable table list")
    has_clients, has_users, has_ledger = (flag == "t" for flag in flags)
    slugs: tuple[str, ...] = ()
    if has_clients:
        listed = _psql(runner, env, DATABASE, "SELECT slug FROM clients ORDER BY slug")
        slugs = tuple(listed.splitlines())
    accounts = 0
    if has_users:
        accounts = _integer(
            _psql(runner, env, DATABASE, "SELECT count(*) FROM console_users"), "account count"
        )
    version = None
    if has_ledger:
        version = _psql(runner, env, DATABASE, "SELECT max(version) FROM schema_migrations") or None
    return StagingState(
        True,
        has_clients,
        slugs,
        accounts,
        database_bytes,
        files,
        sizes,
        version,
    )


def _check_images(sha: str, images: Mapping[str, RunningImage | None]) -> None:
    """Every app container must run the image built from HEAD, so the rebuilt schema fits."""
    problems = []
    for service in APP_SERVICES:
        image = images.get(service)
        if image is None:
            problems.append(f"{service}: its image could not be read")
        elif image.revision is None:
            problems.append(f"{service}: its image has no readable revision")
        elif image.revision != sha:
            problems.append(f"{service}: runs {image.revision[:7]}, HEAD is {sha[:7]}")
    if problems:
        raise RolloutError(
            "images",
            "the services must run the image for HEAD; "
            + "; ".join(problems)
            + f". Roll out HEAD first: {ROLLOUT_PLAN_COMMAND}, then apply.",
        )


def prepare(
    runner: Runner,
    repo: Path,
    *,
    docker_config: Path,
    roster_path: Path = ROSTER_PATH,
    free_bytes: Callable[[], int] = _home_free,
) -> ResetContext:
    """Everything ``wipe`` refuses on. A missing database or schema is not a refusal."""
    sha = target_sha(runner, repo)
    try:
        roster = load_roster(roster_path)
    except RosterError as error:
        raise RolloutError("roster", str(error)) from error
    flags = load_flags(repo / FLAGS_FILE)
    placeholder = {"python": PLACEHOLDER_IMAGE, "frontend": PLACEHOLDER_IMAGE}
    images = running_images(
        runner, build_env(flags, placeholder, docker_config=docker_config)
    )  # docker inspect reads no image variable; stopped containers still answer
    _check_images(sha, images)
    python = images[next(name for name in APP_SERVICES if SERVICE_APP[name] == "python")]
    frontend = images[next(name for name in APP_SERVICES if SERVICE_APP[name] == "frontend")]
    assert python is not None and frontend is not None  # _check_images refused otherwise
    env = build_env(
        flags, {"python": python.ref, "frontend": frontend.ref}, docker_config=docker_config
    )
    missing = missing_secrets(secret_paths(runner, env))
    if missing:
        raise RolloutError("secrets", f"missing secret files: {', '.join(missing)}")
    try:
        newest = discover_migrations()[-1][0]
    except (MigrationError, IndexError) as error:
        raise RolloutError("state", "could not read the checkout's migrations") from error
    state = read_state(runner, env, python.ref)
    free = free_bytes()
    needed = state.database_bytes + sum(state.upload_bytes.values()) + DISK_MARGIN
    if free <= needed:
        raise RolloutError(
            "disk",
            f"not enough free disk for the backup: {_size(free)} free, "
            f"{_size(needed)} needed (backup {_size(needed - DISK_MARGIN)} + "
            f"{_size(DISK_MARGIN)} margin)",
        )
    return ResetContext(sha, env, python.ref, roster, state, newest, free)


def _size(count: int) -> str:
    value = float(count)
    for unit in ("B", "KiB", "MiB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def _roles(roster: Roster) -> list[str]:
    lines = [f"  platform admins: {len(roster.platform_admins)}"]
    for company in roster.companies:
        counts = Counter(member.role for member in company.members)
        roles = ", ".join(f"{counts[role]} {role}" for role in sorted(counts))
        mode = company.deployment_mode or "shadow"
        packages = ", ".join(company.ordered_packages())
        lines.append(
            f"  {company.slug} ({company.name}): packages {packages}; mode {mode}; {roles}"
        )
    return lines


def format_plan(context: ResetContext) -> str:
    """The read-only plan as text: counts and slugs, never an address."""
    state = context.state
    lines = [
        "Staging reset plan (read-only; nothing was changed).",
        f"Target SHA: {context.sha[:7]}; HEAD is on origin/main, clean, and all four services "
        "run its image.",
        "Running images (the rebuilt schema must fit them):",
        f"  python (gateway, worker, delivery): {context.python_image}",
        f"  frontend: {context.env['CSA_STAGING_FRONTEND_IMAGE']}",
    ]
    if not state.database_exists:
        lines.append(f"Database {DATABASE}: does not exist (a wipe that stopped part way?).")
        lines.append("There is nothing in it to back up.")
        lines.append(f"Schema: none; the rebuild will create it up to {context.newest_migration}.")
    elif not state.has_schema:
        lines.append(f"Database {DATABASE}: {_size(state.database_bytes)}, with no schema.")
        lines.append("It will be backed up as it is.")
        lines.append(f"Schema: none; the rebuild will create it up to {context.newest_migration}.")
    else:
        companies = ", ".join(state.slugs) if state.slugs else "none"
        lines.append(f"Database {DATABASE}: {_size(state.database_bytes)}.")
        lines.append(f"Companies ({len(state.slugs)}): {companies}")
        lines.append(f"Accounts: {state.accounts}")
        lines.append(
            f"Schema: now {state.schema_version or 'no migrations recorded'}; "
            f"the rebuild will create it up to {context.newest_migration}."
        )
    lines.append("Upload volumes:")
    for volume in UPLOAD_VOLUMES:
        lines.append(
            f"  {volume}: {state.upload_files[volume]} files, {_size(state.upload_bytes[volume])}"
        )
    lines.append("Roster to create after the wipe (addresses stay in the roster file):")
    lines += _roles(context.roster)
    lines.append("Lost by the wipe and to be redone by hand:")
    lines += [f"  {item}" for item in LOST]
    backup = state.database_bytes + sum(state.upload_bytes.values())
    lines.append(
        f"Disk: {_size(context.free_bytes)} free; the backup takes about {_size(backup)}, "
        f"plus {_size(DISK_MARGIN)} kept free."
    )
    lines.append(f"Next, at a terminal: {WIPE_COMMAND}")
    return "\n".join(lines)


@dataclass
class _Stage:
    """How far a wipe has got: what a stop says about staging, and where the backups are."""

    state: str = UNCHANGED
    backup: Path | None = None
    earlier: tuple[Path, ...] = ()  # backups already on disk when this wipe started, newest first
    intact: bool = False  # services are all that was touched, so starting them brings staging back


class BackupManifest(BaseModel):
    """What one backup holds. It is written last, so a directory without one is incomplete.

    Counts and image refs only: no address, no slug and no secret.
    """

    model_config = ConfigDict(frozen=True)

    sha: str
    python_image: str
    frontend_image: str
    schema_version: str | None
    companies: int
    accounts: int
    upload_files: dict[str, int]
    dump_skipped: bool


def _read_manifest(directory: Path) -> BackupManifest | None:
    try:
        return BackupManifest.model_validate_json((directory / MANIFEST_FILE).read_bytes())
    except (OSError, ValidationError):
        return None


def _write_manifest(context: ResetContext, directory: Path) -> None:
    state = context.state
    manifest = BackupManifest(
        sha=context.sha,
        python_image=context.python_image,
        frontend_image=context.env[IMAGE_ENV["frontend"]],
        schema_version=state.schema_version,
        companies=len(state.slugs),
        accounts=state.accounts,
        upload_files=state.upload_files,
        dump_skipped=not state.database_exists,
    )
    # Renamed into place, so a half-written manifest can't pass for a finished backup.
    temporary = directory / f"{MANIFEST_FILE}.tmp"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(manifest.model_dump_json(indent=2))
        temporary.rename(directory / MANIFEST_FILE)
    except OSError as error:
        raise RolloutError(
            "backup", f"could not write {MANIFEST_FILE}: {error.strerror}"
        ) from error


def _backup_line(directory: Path) -> str:
    manifest = _read_manifest(directory)
    if manifest is None:
        return f"{directory}: {INCOMPLETE}"
    line = (
        f"{directory}: revision {manifest.sha[:7]}, "
        f"schema {manifest.schema_version or 'none'}, "
        f"companies {manifest.companies}, accounts {manifest.accounts}, "
        f"upload files {sum(manifest.upload_files.values())}"
    )
    return f"{line}, no database dump" if manifest.dump_skipped else line


def _holds_real_data(directory: Path) -> bool:
    manifest = _read_manifest(directory)
    return manifest is not None and manifest.companies > 0


def _report_backups(stage: _Stage, out: Callable[[str], None]) -> None:
    """List the earlier backups, and say which one to restore if this run's holds no real data."""
    if stage.earlier:
        out("Earlier backups, kept as they were:")
        for directory in stage.earlier:
            out(f"  {_backup_line(directory)}")
    if (
        stage.state == UNCHANGED
        or stage.intact
        or (stage.backup is not None and _holds_real_data(stage.backup))
    ):
        return  # staging still has its data, or this run's backup does
    found = next((path for path in stage.earlier if _holds_real_data(path)), None)
    if found is None:
        out("No complete backup here holds any companies.")
    else:
        out(
            f"The most recent complete backup with real data is {found}. "
            "Restore that one to get the old data back."
        )


def _report_wipe_stop(error: RolloutError, stage: _Stage, out: Callable[[str], None]) -> None:
    _report_stop(error, out)
    out(stage.state)
    if stage.backup is not None:
        out(f"This run's backup: {_backup_line(stage.backup)}")
    _report_backups(stage, out)
    if stage.state == UNCHANGED:
        out("Fix the cause and run wipe again.")
        return
    if stage.intact:
        out(INTACT)
        out(RESTART_COMMAND)
        return
    out(RECOVERY)
    out(MOVED_HEAD)


def _backup_to_file(
    runner: ResetRunner, env: Mapping[str, str], argv: Sequence[str], path: Path
) -> None:
    code = runner.run_to_file(argv, env=env, path=path)
    if code != 0:
        raise RolloutError("backup", f"writing {path.name} failed (exit {code})")
    if path.stat().st_size == 0:
        raise RolloutError("backup", f"{path.name} came out empty")


def _back_up(
    runner: ResetRunner,
    context: ResetContext,
    stage: _Stage,
    clock: Callable[[], datetime],
    out: Callable[[str], None],
) -> None:
    """Write a new directory of backups. An existing directory is never reused or touched."""
    root = BACKUP_ROOT
    directory = root / clock().strftime("%Y%m%dT%H%M%SZ")
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        stage.earlier = tuple(sorted((p for p in root.iterdir() if p.is_dir()), reverse=True))
        directory.mkdir(mode=0o700)
    except OSError as error:
        raise RolloutError("backup", f"could not create {directory}: {error.strerror}") from error
    stage.backup = directory
    out(f"Backing up to {directory}…")
    if context.state.database_exists:
        _backup_to_file(
            runner,
            context.env,
            [*compose_base(), "exec", "-T", "staging-db", *PG_DUMP_COMMAND],
            directory / DUMP_FILE,
        )
    else:
        out(f"{DATABASE} does not exist, so there is nothing to dump; skipping pg_dump.")
    for volume in UPLOAD_VOLUMES:
        _backup_to_file(
            runner,
            context.env,
            _volume_run(
                context.python_image, f"{volume}:/data:ro", "tar", "-C", "/data", "-cf", "-", "."
            ),
            directory / f"{volume}.tar",
        )
    _write_manifest(context, directory)
    out(f"Backup finished: {_backup_line(directory)}")


def _step_with_reason(
    runner: Runner,
    argv: Sequence[str],
    env: Mapping[str, str],
    step: str,
    what: str,
    *,
    input: str | None = None,  # noqa: A002
) -> Completed:
    """Run a step that handles no secret; a stop carries the tail of its stderr."""
    result = runner.run(argv, env=env, input=input)
    if result.returncode != 0:
        raise RolloutError(
            step, _with_reason(f"{what} failed (exit {result.returncode})", result.stderr)
        )
    return result


def _load_baseline(
    runner: ResetRunner, env: Mapping[str, str], python_image: str, stage: _Stage
) -> None:
    """Load the base schema from the running image, into a database proven to have no tables."""
    stage.state = (
        f"{SERVICES_STOPPED}; {DATABASE} has no schema, or tables nobody expected; {UPLOADS_EMPTY}"
    )
    if _psql(runner, env, DATABASE, NO_TABLES_SQL) != "t":
        raise RolloutError(
            "baseline", f"{DATABASE} already has tables, so the base schema was not loaded"
        )
    baseline = _step_with_reason(
        runner,
        [*docker_base(), *SEALED_RUN, "--entrypoint", "cat", python_image, BASELINE_IN_IMAGE],
        env,
        "baseline",
        "reading the base schema from the image",
    )
    if not baseline.stdout.strip():
        raise RolloutError("baseline", "the image's base schema is empty")
    stage.state = (
        f"{SERVICES_STOPPED}; {DATABASE} has no schema, or only part of the base schema; "
        f"{UPLOADS_EMPTY}"
    )
    _step_with_reason(
        runner,
        _psql_argv(DATABASE, "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"),
        env,
        "baseline",
        "loading the base schema",
        input=baseline.stdout,
    )


def _database_count(runner: Runner, env: Mapping[str, str]) -> str | None:
    """How many databases have the name, as read; ``None`` when the read itself fails."""
    try:
        return _psql(runner, env, MAINTENANCE_DATABASE, DATABASE_COUNT_SQL)
    except RolloutError:
        return None


def _drop_database(runner: Runner, env: Mapping[str, str], stage: _Stage) -> None:
    """Drop the database. An unclear end may still have finished it on the server.

    So staging counts as intact again only if the drop failed with an exit code and a read then
    finds the database. An interrupt skips the read: the server could still be working on it.
    """
    intact = stage.intact
    stage.intact = False
    stage.state = MAYBE_DROPPED
    try:
        _step_with_reason(
            runner,
            _psql_argv(MAINTENANCE_DATABASE, "-v", "ON_ERROR_STOP=1", "-c", DROP_DATABASE_SQL),
            env,
            "drop",
            "DROP DATABASE",
        )
    except RolloutError:
        count = _database_count(runner, env)
        if count == "1":
            stage.intact = intact
            stage.state = NOT_DROPPED
        elif count == "0":
            stage.state = DROPPED
        raise
    stage.state = DROPPED


def _create_first_admin(
    runner: ResetRunner,
    env: Mapping[str, str],
    email: str,
    display_name: str,
    out: Callable[[str], None],
) -> None:
    """Run the bootstrap's prompts. A mistyped or short password need not cost a whole wipe."""
    argv = [
        *compose_base(),
        "--profile",
        "admin-bootstrap-approved",
        "run",
        "--rm",
        "--no-deps",
        "admin-bootstrap",
        "--email",
        email,
        "--display-name",
        display_name,
        "--create-first-platform-admin",
    ]
    approved = {**env, BOOTSTRAP_APPROVAL[0]: BOOTSTRAP_APPROVAL[1]}
    code = runner.run_tty(argv, env=approved)
    for attempt in range(2, BOOTSTRAP_TRIES + 1):
        if code == 0:
            return
        out(
            f"The first platform admin was not created (exit {code}); try again, attempt "
            f"{attempt} of {BOOTSTRAP_TRIES}. The password needs at least 12 characters, typed "
            "the same both times."
        )
        code = runner.run_tty(argv, env=approved)
    if code != 0:
        raise RolloutError("bootstrap", f"admin-bootstrap exited {code}")


def _migrate_argv() -> list[str]:
    return [
        *compose_base(),
        "--profile",
        "migration-approved",
        "run",
        "--rm",
        "--no-deps",
        "migrate",
    ]


def _exception_class(stderr: str) -> str | None:
    """The class on stderr's last line, when it is a dotted identifier. Never what follows it."""
    lines = [line for line in stderr.splitlines() if line.strip()]
    if not lines:
        return None
    name, colon, _ = lines[-1].partition(":")
    parts = name.split(".")
    if colon and len(parts) > 1 and all(part.isidentifier() for part in parts):
        return name
    return None


def _manual_migrate_command(env: Mapping[str, str]) -> str:
    """One copy-ready line that runs migrate as ``wipe`` did, printing to the owner's terminal.

    It spells out the non-secret environment, since the docs' ``csa_pipeline_compose`` exists
    only in a shell that ran their setup. This run's docker config is left out: it is a temp
    directory gone when ``wipe`` exits, and no pull is needed.
    """
    assignments = {MIGRATION_APPROVAL[0]: MIGRATION_APPROVAL[1]} | {
        name: value for name, value in env.items() if name not in {"PATH", "HOME", "DOCKER_CONFIG"}
    }
    words = [f"{name}={shlex.quote(value)}" for name, value in assignments.items()]
    return f"cd {shlex.quote(str(REPO_ROOT))} && {' '.join(words)} {shlex.join(_migrate_argv())}"


def _migrate_failure(stderr: str, env: Mapping[str, str]) -> str:
    """What a failed migrate may say. Its stderr can hold a connection string, so almost nothing."""
    reasons = [line for line in stderr.splitlines() if line.startswith(MIGRATION_ERROR_PREFIX)]
    if reasons:
        return "\n".join(reasons)
    name = _exception_class(stderr)
    return "\n".join(
        [
            *([f"Exception: {name}"] if name else []),
            "Its other error output is not shown, because it can carry connection details. "
            "The lines above show the last migration applied.",
            "To see the full error at this terminal, run:",
            _manual_migrate_command(env),
            "A failed migrate can be re-run safely: migrations already applied are skipped. "
            "That only shows the error; run wipe again to finish the rebuild.",
        ]
    )


def _migrate_and_confirm(
    runner: ResetRunner,
    context: ResetContext,
    stage: _Stage,
    out: Callable[[str], None],
) -> None:
    stage.state = (
        f"{SERVICES_STOPPED}; {DATABASE} has the base schema, and its migrations may be "
        f"unfinished; {UPLOADS_EMPTY}"
    )
    out(f"Migrating {DATABASE}…")
    result = runner.run(
        _migrate_argv(), env={**context.env, MIGRATION_APPROVAL[0]: MIGRATION_APPROVAL[1]}
    )
    if result.stdout.strip():
        out(result.stdout.rstrip())
    if result.returncode != 0:
        raise RolloutError(
            "migrate",
            f"migrate exited {result.returncode}\n{_migrate_failure(result.stderr, context.env)}",
        )
    found = _psql(runner, context.env, DATABASE, "SELECT max(version) FROM schema_migrations")
    if found != context.newest_migration:
        raise RolloutError(
            "version",
            f"schema_migrations stops at {found or 'nothing'}; "
            f"the services expect {context.newest_migration}",
        )


def _rebuild(
    runner: ResetRunner,
    context: ResetContext,
    stage: _Stage,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    out: Callable[[str], None],
) -> None:
    """Every step after the confirmation, in the fixed order. Each sets the state a stop reports."""
    env = context.env
    first = context.roster.platform_admins[0]
    _back_up(runner, context, stage, clock, out)

    # An earlier wipe that stopped before its first admin left no account and no data: starting
    # the services would not bring anything back, so a stop still gives the restore advice.
    stage.intact = context.state.accounts > 0
    stage.state = (
        "some of gateway, frontend, worker and delivery may still be running; check compose ps. "
        f"{DATABASE} and the upload volumes are as they were"
    )
    out(f"Stopping {', '.join(STOP_SERVICES)}…")
    _step_with_reason(
        runner, [*compose_base(), "stop", *STOP_SERVICES], env, "stop", "compose stop"
    )

    stage.state = NOT_DROPPED
    out(f"Dropping and creating {DATABASE}…")
    _drop_database(runner, env, stage)
    _step_with_reason(
        runner,
        _psql_argv(MAINTENANCE_DATABASE, "-v", "ON_ERROR_STOP=1", "-c", CREATE_DATABASE_SQL),
        env,
        "create",
        "CREATE DATABASE",
    )

    stage.state = (
        f"{SERVICES_STOPPED}; {DATABASE} has no schema; the upload volumes may be partly emptied"
    )
    out("Emptying the upload volumes…")
    for volume in UPLOAD_VOLUMES:
        _step_with_reason(
            runner,
            _volume_run(context.python_image, f"{volume}:/data", "sh", "-c", EMPTY_VOLUME_SCRIPT),
            env,
            "empty volumes",
            f"emptying {volume}",
        )

    out("Loading the base schema…")
    _load_baseline(runner, env, context.python_image, stage)
    _migrate_and_confirm(runner, context, stage, out)

    stage.state = (
        f"{SERVICES_STOPPED}; {DATABASE} is at {context.newest_migration} with no platform admin; "
        f"{UPLOADS_EMPTY}"
    )
    out("Creating the first platform admin; type an initial password when asked.")
    _create_first_admin(runner, env, first.email, first.display_name, out)

    stage.state = (
        f"{DATABASE} is at {context.newest_migration} with its first platform admin; "
        f"{UPLOADS_EMPTY}"
    )
    out(f"Starting {', '.join(APP_SERVICES)}…")
    for service in APP_SERVICES:
        expected = env[IMAGE_ENV[SERVICE_APP[service]]]
        _recreate(runner, env, service, expected, sleep, out, stopped=True)
    _await_ready(runner, env, sleep)


def _on_a_terminal() -> bool:
    """Input and output both. Compose allocates a TTY only when stdout is a terminal, and without
    one the password prompts echo. What these commands print also names people, so no pipes.
    """
    return sys.stdin.isatty() and sys.stdout.isatty()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def wipe(
    runner: ResetRunner,
    repo: Path,
    *,
    docker_config: Path,
    read_line: Callable[[str], str] = input,
    is_terminal: Callable[[], bool] = _on_a_terminal,
    free_bytes: Callable[[], int] = _home_free,
    clock: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> int:
    """Back staging up, then drop and rebuild it. Only the typed phrase lets it past reading."""
    if not is_terminal():
        out(
            "wipe must be run by the owner at a real terminal, with no pipe on its input or "
            "output. Nothing on staging was changed."
        )
        return 1
    try:
        context = prepare(
            runner,
            repo,
            docker_config=docker_config,
            roster_path=ROSTER_PATH,
            free_bytes=free_bytes,
        )
    except RolloutError as error:
        _report_stop(error, out)
        return 1
    out(format_plan(context))
    try:
        typed = read_line(f"Type {CONFIRM_PHRASE} to go ahead: ")
    except EOFError:
        typed = ""
    if typed != CONFIRM_PHRASE:
        out("That is not the phrase.")
        out(UNCHANGED)
        return 1
    stage = _Stage()
    try:
        _rebuild(runner, context, stage, clock, sleep, out)
    except RolloutError as error:
        _report_wipe_stop(error, stage, out)
        return 1
    except KeyboardInterrupt:
        _report_wipe_stop(RolloutError("interrupt", "the wipe was interrupted"), stage, out)
        return 1
    first = context.roster.platform_admins[0]
    assert stage.backup is not None  # _rebuild starts by making it
    out(
        f"Staging is rebuilt at {context.newest_migration}. "
        f"This run's backup: {_backup_line(stage.backup)}"
    )
    _report_backups(stage, out)
    out(
        f"Next: sign in as {first.email}, change the password, then run, at a terminal: "
        f"{ACCOUNTS_COMMAND}"
    )
    return 0


def company_step_input(password: str, roster: Roster) -> str:
    """What ``scripts.staging_accounts`` reads on stdin: a line of password, then the roster."""
    return f"{password}\n{roster.model_dump_json()}"


def _admin_exists(runner: Runner, env: Mapping[str, str], email: str) -> bool:
    quoted = email.replace("'", "''")
    count = _psql(
        runner,
        env,
        DATABASE,
        f"SELECT count(*) FROM console_users WHERE email = '{quoted}' AND is_platform_admin",
    )
    return _integer(count, "admin count") > 0


def _add_admins(runner: ResetRunner, context: ResetContext, out: Callable[[str], None]) -> None:
    """Add each further admin. One already there costs no prompt and no throttled attempt."""
    first, *further = context.roster.platform_admins
    for admin in further:
        if _admin_exists(runner, context.env, admin.email):
            out(f"platform admin {admin.email}: already there")
            continue
        out(
            f"Adding platform admin {admin.email}: type {first.email}'s password, "
            "then an initial password for them to change at first sign-in."
        )
        code = runner.run_tty(
            [
                *compose_base(),
                "--profile",
                "admin-add-approved",
                "run",
                "--rm",
                "--no-deps",
                "admin-add",
                "--approver-email",
                first.email,
                "--email",
                admin.email,
                "--display-name",
                admin.display_name,
                "--add-platform-admin",
            ],
            env={**context.env, ADMIN_ADD_APPROVAL[0]: ADMIN_ADD_APPROVAL[1]},
        )
        if code != 0:
            raise RolloutError(
                "admin-add",
                f"admin-add exited {code}. The first platform admin must have signed in and "
                "changed their password before another admin can be added, and that password "
                "must be typed correctly (5 attempts per 15 minutes). The address must also be "
                "free. No company was created; fix this and run accounts again.",
            )


def _company_step(
    runner: Runner,
    context: ResetContext,
    password: str,
    out: Callable[[str], None],
) -> int:
    """Run the company step in the gateway; its stdout goes to the terminal, nowhere else."""
    out("Creating the companies inside the gateway; the lines come when it has finished.")
    result = runner.run(
        [
            *docker_base(),
            "exec",
            "-i",
            GATEWAY_CONTAINER,
            "python",
            "-m",
            "scripts.staging_accounts",
        ],
        env=context.env,
        input=company_step_input(password, context.roster),
    )
    if result.stdout.strip():
        out(result.stdout.rstrip())
    if result.returncode == 2:
        out("Use Renew invitation on the Team page for each invitation that was not mailed.")
    elif result.returncode != 0:
        # Its stderr is not shown: that process was handed the password.
        out(
            f"The company step exited {result.returncode}. If no reason is shown above, check "
            f"that {GATEWAY_CONTAINER} is running."
        )
    return result.returncode


def accounts(
    runner: ResetRunner,
    repo: Path,
    *,
    docker_config: Path,
    is_terminal: Callable[[], bool] = _on_a_terminal,
    read_password: Callable[[str], str] = getpass.getpass,
    out: Callable[[str], None] = print,
) -> int:
    """Add the further platform admins, then create the roster's companies inside the gateway."""
    if not is_terminal():
        out("accounts must be run by the owner at a real terminal. Nothing was created.")
        return 1
    try:
        context = prepare(
            runner,
            repo,
            docker_config=docker_config,
            roster_path=ROSTER_PATH,
            free_bytes=_home_free,
        )
        if not context.state.has_schema:
            raise RolloutError("state", f"{DATABASE} has no schema; run wipe first")
        wanted = {company.slug for company in context.roster.companies}
        outside = [slug for slug in context.state.slugs if slug not in wanted]
        if outside:
            raise RolloutError(
                "state",
                f"companies outside the roster: {', '.join(outside)}; this staging was not "
                "wiped. Nothing was created",
            )
        first = context.roster.platform_admins[0]
        password = read_password(f"Password of {first.email}: ")
        # A control character or undecodable byte could not arrive as one line of stdin, and
        # an encoding error would quote part of it. The text here never repeats the password.
        if not password or not password.isprintable():
            raise RolloutError("password", "the password is empty or cannot be sent as one line")
        _add_admins(runner, context, out)
        return _company_step(runner, context, password, out)
    except RolloutError as error:
        _report_stop(error, out)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts.staging_reset", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="show what a reset would lose and build (read-only)")
    commands.add_parser("wipe", help="back up, wipe and rebuild staging (owner, at a terminal)")
    commands.add_parser("accounts", help="create the roster's accounts (owner, at a terminal)")
    return parser


def main(argv: list[str] | None = None, runner: ResetRunner | None = None) -> int:
    """Run a command; the targets are fixed, so the only arguments are the command names."""
    args = _parser().parse_args(argv)
    runner = runner or SubprocessResetRunner(cwd=REPO_ROOT)
    docker_config = Path(tempfile.mkdtemp(prefix="csa-reset-docker-"))
    try:
        docker_config.chmod(0o700)
        if args.command == "wipe":
            return wipe(
                runner,
                REPO_ROOT,
                docker_config=docker_config,
                read_line=input,
                is_terminal=_on_a_terminal,
                free_bytes=_home_free,
            )
        if args.command == "accounts":
            return accounts(
                runner,
                REPO_ROOT,
                docker_config=docker_config,
                is_terminal=_on_a_terminal,
                read_password=getpass.getpass,
            )
        context = prepare(
            runner,
            REPO_ROOT,
            docker_config=docker_config,
            roster_path=ROSTER_PATH,
            free_bytes=_home_free,
        )
    except RolloutError as error:
        _report_stop(error, print)
        return 1
    finally:
        shutil.rmtree(docker_config, ignore_errors=True)
    print(format_plan(context))
    return 0


if __name__ == "__main__":
    sys.exit(main())
