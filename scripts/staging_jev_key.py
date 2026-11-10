"""Privately provision the staging Jev key (TYPESAFE_API_KEY) without replacing an existing one.

    uv run python -m scripts.staging_jev_key --secret-dir /srv/csa-staging/secrets

The owner runs it as the runtime UID (1000), only after ``scripts.assistant_jev_eval`` passes
(docs/RUNBOOK.md, Jev). It checks the existing secret layout first (``staging_secrets check``),
then writes ``pipeline/staging_jev_key`` owner-only (mode 0400, no trailing newline). It never
overwrites the file or follows a symlink in its place, reads the key from a hidden prompt and never
prints it. To rotate the key, remove the old file and run this again.
"""

from __future__ import annotations

import argparse
import getpass
import os
import stat
from collections.abc import Sequence
from pathlib import Path

from pydantic import SecretStr

from scripts import staging_secrets

# Values Settings reads as "no key": a file holding one would leave every area on the LLM.
PLACEHOLDERS = frozenset({"disabled", staging_secrets.DISABLED_MODEL_KEY})


def provision(secret_dir: Path, value: SecretStr) -> str:
    """Write the key file and return its path relative to the secret directory.

    Errors never include the value.
    """
    target = secret_dir / staging_secrets.JEV_KEY_FILE
    if target.exists() or target.is_symlink():
        raise FileExistsError("Staging Jev key already exists; refusing to overwrite")
    if secret_dir.is_symlink() or staging_secrets.check(secret_dir, os.getuid()):
        raise staging_secrets.ProvisionError("Existing staging secret layout must pass check")
    pipeline = target.parent
    pipeline.mkdir(mode=0o700, exist_ok=True)
    if (
        pipeline.is_symlink()
        or pipeline.stat().st_uid != os.getuid()
        or stat.S_IMODE(pipeline.stat().st_mode) != 0o700
    ):
        raise staging_secrets.ProvisionError("Pipeline secret directory must be private")
    raw = value.get_secret_value().strip()
    if not raw or any(character.isspace() for character in raw) or raw in PLACEHOLDERS:
        raise staging_secrets.ProvisionError("Enter the real Jev key, one word with no spaces")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    try:
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o400)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        target.unlink()
        raise
    return staging_secrets.JEV_KEY_FILE


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.staging_jev_key", description=__doc__)
    parser.add_argument("--secret-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        value = SecretStr(getpass.getpass("Staging Jev key, TYPESAFE_API_KEY (hidden): "))
        written = provision(args.secret_dir, value)
    except (OSError, EOFError, staging_secrets.ProvisionError):
        print(
            "Key not written. Check the secret layout (staging_secrets check), the key input and "
            "whether pipeline/staging_jev_key already exists."
        )
        return 1
    print(f"created {written} (0400); no value printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
