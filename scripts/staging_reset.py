"""Reset staging with real accounts: ``plan`` is read-only; ``wipe`` and ``accounts`` come later.

``plan`` re-derives everything ``wipe`` will refuse on (a clean checkout on origin/main, every
service running the image built from HEAD, a valid roster, every secret file present, enough
disk for the backup) and prints what is about to be lost. It runs only read commands: git,
``docker inspect``, ``compose config``, ``psql`` with ``SELECT``, and one-off containers with no
network and read-only mounts that count the files in each upload volume.

The targets are constants. Nothing here takes the project, the database, the host or a volume
from an argument or the environment, and the roster's addresses are never printed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from scripts.migrate import MigrationError, discover_migrations
from scripts.staging_rollout import (
    APP_SERVICES,
    FLAGS_FILE,
    REPO_ROOT,
    SERVICE_APP,
    RolloutError,
    Runner,
    RunningImage,
    SubprocessRunner,
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
VOLUME_SCRIPT = "find /data -type f | wc -l; du -sb /data"
LOST = (
    "every account and session",
    "all HR and customer data",
    "knowledge base files",
    "Telegram, WhatsApp and Gmail connections",
)
WIPE_COMMAND = ".venv/bin/python -m scripts.staging_reset wipe"
PLACEHOLDER_IMAGE = "unused-until-the-running-images-are-read"


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


def _psql(runner: Runner, env: Mapping[str, str], database: str, sql: str) -> str:
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
            database,
            "-tA",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        env=env,
    )
    if result.returncode != 0:
        raise RolloutError(
            "state", f"could not read staging with psql on {database} (exit {result.returncode})"
        )
    return result.stdout.strip()


def _integer(text: str, what: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise RolloutError("state", f"staging returned an unreadable {what}") from None


def _volume_usage(
    runner: Runner, env: Mapping[str, str], python_image: str, volume: str
) -> tuple[int, int]:
    """File count and bytes of one upload volume, read through a throwaway container."""
    # ``docker run -v name:`` creates a missing volume, so look first.
    exists = runner.run([*docker_base(), "volume", "inspect", volume], env=env)
    if exists.returncode != 0:
        raise RolloutError("state", f"volume {volume} does not exist")
    result = runner.run(
        [
            *docker_base(),
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "1000:1000",
            "-v",
            f"{volume}:/data:ro",
            "--entrypoint",
            "sh",
            python_image,
            "-c",
            VOLUME_SCRIPT,
        ],
        env=env,
    )
    lines = result.stdout.split("\n")
    if result.returncode != 0 or len(lines) < 2 or not lines[1].split():
        raise RolloutError("state", f"could not read volume {volume} (exit {result.returncode})")
    return _integer(lines[0].strip(), "file count"), _integer(lines[1].split()[0], "volume size")


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
            "the services must run the image for HEAD; " + "; ".join(problems),
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
    if args.command != "plan":
        print(f"{args.command} is not available in this version.")
        return 1
    runner = runner or SubprocessResetRunner(cwd=REPO_ROOT)
    docker_config = Path(tempfile.mkdtemp(prefix="csa-reset-docker-"))
    try:
        docker_config.chmod(0o700)
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
