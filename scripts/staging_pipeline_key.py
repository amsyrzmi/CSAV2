"""Privately provision the separate staging model key without replacing existing secrets."""

from __future__ import annotations

import argparse
import getpass
import os
import stat
from pathlib import Path

from pydantic import SecretStr

from scripts import staging_secrets


def provision(secret_dir: Path, value: SecretStr) -> None:
    target = secret_dir / "pipeline/staging_model_key"
    if target.exists() or target.is_symlink():
        raise FileExistsError("Staging model key already exists; refusing to overwrite")
    if secret_dir.is_symlink() or staging_secrets.check(secret_dir, os.getuid()):
        raise staging_secrets.ProvisionError("Existing staging secret layout must pass check")
    runtime = secret_dir / "pipeline"
    runtime.mkdir(mode=0o700, exist_ok=True)
    if (
        runtime.is_symlink()
        or runtime.stat().st_uid != os.getuid()
        or stat.S_IMODE(runtime.stat().st_mode) != 0o700
    ):
        raise staging_secrets.ProvisionError("Pipeline secret directory must be private")
    raw = value.get_secret_value()
    if (
        len(raw) < 20
        or raw != raw.strip()
        or "\n" in raw
        or "\r" in raw
        or raw == staging_secrets.DISABLED_MODEL_KEY
    ):
        raise staging_secrets.ProvisionError(
            "Enter a real staging-only Groq key without whitespace"
        )
    descriptor = os.open(
        runtime / "staging_model_key", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400
    )
    try:
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o400)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        (runtime / "staging_model_key").unlink()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        provision(args.secret_dir, SecretStr(getpass.getpass("Staging-only Groq key (hidden): ")))
    except (OSError, staging_secrets.ProvisionError):
        print(
            "Key not provisioned. Check the secret layout, key input and existing-file protection."
        )
        return 1
    print("Staging model key provisioned (owner-only); no value printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
