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
from pathlib import Path

CONFIG_NAME = "config.json"


def _usable(data: bytes) -> bool:
    """A Docker config: a JSON object whose ``auths`` is an object."""
    try:
        config = json.loads(data.decode("utf-8"))
    except ValueError:  # includes UnicodeDecodeError and JSONDecodeError
        return False
    return isinstance(config, dict) and isinstance(config.get("auths"), dict)


def install(docker_config: Path, source: Path) -> bool:
    """Copy ``source`` to ``docker_config/config.json`` (mode 0600); False when it is absent.

    Raises ``RolloutError`` at step ``plan`` when the file exists but is empty, unreadable or
    not a Docker config. The error is raised outside any ``except`` block, so it chains no
    exception that could hold the content.
    """
    # Imported here: scripts.staging_rollout imports this module.
    from scripts.staging_rollout import RolloutError

    try:
        data = source.read_bytes()
    except FileNotFoundError:
        return False
    except OSError:  # unreadable, a directory, a parent that is not a directory
        data = b""
    if not data or not _usable(data):
        raise RolloutError("plan", f"registry auth file {source} is not usable")
    # O_EXCL: never write through a file already there; 0600 before a byte is written.
    descriptor = os.open(docker_config / CONFIG_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    return True
