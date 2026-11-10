"""Rollout targets: everything that differs between one deployment and another.

``scripts.staging_rollout`` reads these values instead of hard-coding them, so a second
target is a new ``Target`` and not a second copy of the rollout logic. This module
imports nothing from the rollout script.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Target:
    name: str  # "staging" | "production"
    project: str  # compose project
    secret_dir: Path
    compose_files: tuple[str, ...]  # repo-relative, in --file order
    profiles: tuple[str, ...]
    image_owner: str  # registry path, e.g. "ghcr.io/<owner>"
    package_owner: str  # for gh api /users/<owner>/packages/...
    repo: str  # "<owner>/<repo>", for the deployments API
    app_services: tuple[str, ...]  # recreate order
    stop_services: tuple[str, ...]
    replicas: Mapping[str, tuple[str, int]]  # service -> (env var, default count); {} = one each
    db_service: str
    db_name: str
    db_user: str
    flags_file: str  # repo-relative
    required_keys: frozenset[str]
    optional_keys: frozenset[str]
    host_file: Path | None
    host_required_keys: frozenset[str]
    host_override_keys: frozenset[str]  # subset of optional_keys a host file may set
    migration_approval: tuple[str, str]
    staged_gate: bool
    downgrade_warning: bool
    pre_change: tuple[str, ...] | None  # argv run before anything changes
    live_checkout: str  # path printed in rollback text


STAGING = Target(
    name="staging",
    project="csa-staging",
    secret_dir=Path("/srv/csa-staging/secrets"),
    compose_files=(
        "deploy/compose.staging-local-proposal.yml",
        "deploy/compose.staging-shared-caddy.yml",
        "deploy/compose.staging-pipeline.yml",
    ),
    profiles=("staging-approved", "pipeline-approved"),
    image_owner="ghcr.io/amsyrzmi",
    package_owner="amsyrzmi",
    repo="amsyrzmi/CSAV2",
    app_services=("gateway", "worker", "delivery", "frontend"),
    stop_services=("gateway", "frontend", "worker", "delivery"),
    replicas={},
    db_service="staging-db",
    db_name="csa_staging",
    db_user="csa",
    flags_file="deploy/staging-rollout.env",
    required_keys=frozenset(
        {
            "CSA_STAGING_CONSOLE_ORIGIN",
            "CSA_STAGING_HOOKS_ORIGIN",
            "CSA_STAGING_WORKSPACE_ASSISTANT",
            "CSA_STAGING_GATEWAY_MODEL_SECRET",
            "CSA_STAGING_INVITE_MAIL",
            "CSA_STAGING_EMAIL_ASSISTANT",
            "CSA_STAGING_BOOKINGS_GOOGLE_CALENDAR",
            "CSA_STAGING_BOOKINGS_GOOGLE_MEET",
        }
    ),
    optional_keys=frozenset(),
    host_file=None,
    host_required_keys=frozenset(),
    host_override_keys=frozenset(),
    migration_approval=("CSA_STAGING_MIGRATION_APPROVAL", "approved-isolated-staging"),
    staged_gate=False,
    downgrade_warning=False,
    pre_change=None,
    live_checkout="/home/amsyrzmi/CSAV2",
)

_TARGETS: dict[str, Target] = {STAGING.name: STAGING}


def get_target(name: str) -> Target:
    try:
        return _TARGETS[name]
    except KeyError:
        raise ValueError(f"unknown target {name}") from None
