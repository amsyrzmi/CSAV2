"""Optional read-only registry login for the rollout and reset scripts.

Both scripts pull with a temporary, private Docker config directory. When the host has a Docker
auth file at the target's ``registry_auth`` path, ``install`` copies it into that directory, so
the pulls use the login; when it has none, nothing is written and the pulls stay anonymous.

The file is a credential: it is read and copied as bytes, and no message, log line or argv ever
carries its content. A refusal names only the path.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

CONFIG_NAME = "config.json"
MAX_BYTES = 64 * 1024


def _usable(data: bytes) -> bool:
    """Only ``auths``, every entry carrying a non-empty inline ``auth`` string.

    Anything else a ``docker login`` config may hold (``credsStore``, ``credHelpers``,
    ``proxies``, plugin directories, entries without inline ``auth``) is refused: it would
    reach past the private Docker config this file is copied into.
    """
    try:
        config = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):  # ValueError includes UnicodeDecodeError
        return False
    if not isinstance(config, dict) or set(config) != {"auths"}:
        return False
    auths = config["auths"]
    return (
        isinstance(auths, dict)
        and bool(auths)
        and all(
            isinstance(entry, dict) and isinstance(entry.get("auth"), str) and bool(entry["auth"])
            for entry in auths.values()
        )
    )


def _read(source: Path) -> bytes | None:
    """The bytes of ``source``; None when it is absent; b"" when it cannot be used.

    One open with ``O_NOFOLLOW | O_NONBLOCK``: a symlink (dangling or not) fails to open, a FIFO
    opens without blocking, and the type and size checks run on the same descriptor the bytes
    are read from. Only a regular file of at most ``MAX_BYTES`` is read.
    """
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:  # a symlink, unreadable, a parent that is not a directory
        return b""
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_size > MAX_BYTES:
            return b""
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_BYTES:
            chunk = os.read(descriptor, MAX_BYTES + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > MAX_BYTES:  # grew after the fstat
            return b""
        return b"".join(chunks)
    except OSError:
        return b""
    finally:
        os.close(descriptor)


def _write(destination: Path, data: bytes) -> bool:
    """Write ``data`` to a new ``destination`` (mode 0600); False on any OS error."""
    try:
        # O_EXCL: never write through a file already there; 0600 before a byte is written.
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        return False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
    except OSError:
        try:
            destination.unlink()
        except OSError:
            pass
        return False
    return True


def install(docker_config: Path, source: Path) -> bool:
    """Copy ``source`` to ``docker_config/config.json`` (mode 0600); False when it is absent.

    Raises ``RolloutError`` at step ``plan`` when the file exists but is not a regular file of
    at most ``MAX_BYTES``, is empty or unreadable, or is not an ``auths``-only config with an
    inline ``auth`` in every entry; and when the copy cannot be written. Each error is raised
    outside any ``except`` block, so it chains no exception that could hold the content.
    """
    # Imported here: scripts.staging_rollout imports this module.
    from scripts.staging_rollout import RolloutError

    data = _read(source)
    if data is None:
        return False
    if not data or not _usable(data):
        raise RolloutError("plan", f"registry auth file {source} is not usable")
    if not _write(docker_config / CONFIG_NAME, data):
        raise RolloutError("plan", f"could not copy the registry auth file {source}")
    return True
