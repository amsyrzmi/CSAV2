"""Create or check the isolated staging secret-file layout on the staging host.

Host-side operator tool for ``deploy/compose.staging-local-proposal.yml``.
``create`` generates fresh staging-only values into a new directory (never
overwriting) and prints only relative paths. ``check`` verifies layout,
ownership, modes, DSN targets and cross-file consistency without printing any
value. The Cloudflare tunnel token is deliberately out of scope: it is issued by
Cloudflare at the separately approved tunnel gate and must be owned by
``65532:65532``.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import os
import secrets
import shutil
import stat
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
HOST = "staging-db"
DATABASE = "csa_staging"
DISABLED_MODEL_KEY = "staging-disabled-no-model-access"
DIRECTORIES = ("database", "migration", "runtime")
FILES = (
    "database/staging_owner_password",
    "migration/staging_owner_dsn",
    "migration/staging_app_password",
    "migration/staging_ro_password",
    "migration/staging_identity_password",
    "runtime/staging_domain_dsn",
    "runtime/staging_checkpoint_dsn",
    "runtime/staging_identity_dsn",
    "runtime/staging_credentials_key",
    "runtime/staging_disabled_model_key",
)
DSN_ROLES = {
    "migration/staging_owner_dsn": ("csa", "database/staging_owner_password"),
    "runtime/staging_domain_dsn": ("csa_app", "migration/staging_app_password"),
    "runtime/staging_checkpoint_dsn": ("csa_app", "migration/staging_app_password"),
    "runtime/staging_identity_dsn": ("csa_identity", "migration/staging_identity_password"),
}
PASSWORDS = (
    "database/staging_owner_password",
    "migration/staging_app_password",
    "migration/staging_ro_password",
    "migration/staging_identity_password",
)


class ProvisionError(Exception):
    """Raised when the secret directory is unsafe or inconsistent."""


def _dsn(role: str, password: str) -> str:
    return f"postgresql://{role}:{password}@{HOST}:5432/{DATABASE}"


def generate() -> dict[str, str]:
    """Return fresh values keyed by relative path; hex passwords need no URL quoting."""
    values = {name: secrets.token_hex(24) for name in PASSWORDS}
    for name, (role, password_file) in DSN_ROLES.items():
        values[name] = _dsn(role, values[password_file])
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    values["runtime/staging_credentials_key"] = f"base64:{key}"
    values["runtime/staging_disabled_model_key"] = DISABLED_MODEL_KEY
    return values


def _validate_target(secret_dir: Path) -> Path:
    if not secret_dir.is_absolute():
        raise ProvisionError("secret directory must be an absolute path")
    resolved = secret_dir.resolve()
    if resolved.is_relative_to(REPO_ROOT):
        raise ProvisionError("secret directory must be outside the source checkout")
    return resolved


def create(secret_dir: Path, runtime_uid: int) -> list[str]:
    """Write a complete new layout atomically; refuse to touch an existing path."""
    if os.geteuid() != runtime_uid:
        raise ProvisionError(f"run as the container runtime UID {runtime_uid}")
    target = _validate_target(secret_dir)
    if target.exists() or target.is_symlink():
        raise ProvisionError("secret directory already exists; refusing to overwrite")
    if not target.parent.is_dir():
        raise ProvisionError("parent of the secret directory must already exist")
    staging = target.parent / f".{target.name}.partial-{secrets.token_hex(4)}"
    os.mkdir(staging, 0o700)
    try:
        for directory in DIRECTORIES:
            os.mkdir(staging / directory, 0o700)
        for name, value in generate().items():
            fd = os.open(staging / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
            try:
                os.write(fd, value.encode())
                os.fchmod(fd, 0o400)
            finally:
                os.close(fd)
        for directory in DIRECTORIES:
            os.chmod(staging / directory, 0o700)
        os.chmod(staging, 0o700)
        os.rename(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return sorted(FILES)


def _read(path: Path, runtime_uid: int, failures: list[str], name: str) -> str | None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        failures.append(f"{name}: not a regular file")
        return None
    if info.st_uid != runtime_uid:
        failures.append(f"{name}: owner UID is not {runtime_uid}")
    if stat.S_IMODE(info.st_mode) != 0o400:
        failures.append(f"{name}: mode is not 0400")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        failures.append(f"{name}: empty")
        return None
    return value


def check(secret_dir: Path, runtime_uid: int) -> list[str]:
    """Return failure descriptions that never include secret values."""
    target = _validate_target(secret_dir)
    failures: list[str] = []
    for directory, relative in ((target, "."), *((target / d, d) for d in DIRECTORIES)):
        if directory.is_symlink() or not directory.is_dir():
            failures.append(f"{relative}: missing directory")
            continue
        info = directory.lstat()
        if info.st_uid != runtime_uid:
            failures.append(f"{relative}: owner UID is not {runtime_uid}")
        if stat.S_IMODE(info.st_mode) != 0o700:
            failures.append(f"{relative}: mode is not 0700")
    if failures:
        return failures
    values: dict[str, str] = {}
    for name in FILES:
        path = target / name
        if not path.exists() and not path.is_symlink():
            failures.append(f"{name}: missing")
            continue
        value = _read(path, runtime_uid, failures, name)
        if value is not None:
            values[name] = value
    for directory in DIRECTORIES:
        for entry in (target / directory).iterdir():
            if f"{directory}/{entry.name}" not in FILES:
                failures.append(f"{directory}/{entry.name}: unexpected entry")
    for name in PASSWORDS:
        if name in values and len(values[name]) < 32:
            failures.append(f"{name}: shorter than 32 characters")
    for name, (role, password_file) in DSN_ROLES.items():
        if name not in values:
            continue
        url = urlsplit(values[name])
        if (
            url.scheme not in {"postgres", "postgresql"}
            or url.hostname != HOST
            or url.port not in {None, 5432}
            or url.username != role
            or url.path != f"/{DATABASE}"
            or url.query
            or url.fragment
        ):
            failures.append(f"{name}: must be {role}@{HOST}:5432/{DATABASE}")
        elif password_file in values and unquote(url.password or "") != values[password_file]:
            failures.append(f"{name}: password does not match {password_file}")
    key = values.get("runtime/staging_credentials_key")
    if key is not None:
        try:
            ok = key.startswith("base64:") and len(base64.b64decode(key[7:], validate=True)) == 32
        except binascii.Error:
            ok = False
        if not ok:
            failures.append("runtime/staging_credentials_key: not base64 of 32 bytes")
    model = values.get("runtime/staging_disabled_model_key")
    if model is not None and model != DISABLED_MODEL_KEY:
        failures.append("runtime/staging_disabled_model_key: not the disabled placeholder")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "check"))
    parser.add_argument("--secret-dir", type=Path, required=True)
    parser.add_argument("--runtime-uid", type=int, default=1000)
    args = parser.parse_args(argv)
    try:
        if args.action == "create":
            for name in create(args.secret_dir, args.runtime_uid):
                print(f"created {name} (0400)")
        failures = check(args.secret_dir, args.runtime_uid)
    except (OSError, ProvisionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if failures:
        return 1
    print(f"OK {len(FILES)} staging secret files; values not displayed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
