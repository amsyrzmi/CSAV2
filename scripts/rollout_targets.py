"""Rollout targets: everything that differs between one deployment and another.

``scripts.staging_rollout`` reads these values instead of hard-coding them, so a second
target is a new ``Target`` and not a second copy of the rollout logic. This module
imports nothing from the rollout script.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
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
    optional_values: Mapping[str, tuple[str, ...]]  # allowed values of some optional keys
    host_file: Path | None
    host_required_keys: frozenset[str]
    host_override_keys: frozenset[str]  # optional keys a host file may set (its value wins)
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
    app_services=("gateway", "worker", "delivery", "frontend", "training-worker"),
    stop_services=("gateway", "frontend", "worker", "delivery", "training-worker"),
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
    # Video in the training library (gateway and training worker); unset is off. Turning it on
    # is a reviewed change to the flags file, after the checks in deploy/PRODUCTION.md.
    optional_keys=frozenset({"CSA_STAGING_TRAINING_VIDEO"}),
    optional_values={"CSA_STAGING_TRAINING_VIDEO": ("false", "true")},
    host_file=None,
    host_required_keys=frozenset(),
    host_override_keys=frozenset(),
    migration_approval=("CSA_STAGING_MIGRATION_APPROVAL", "approved-isolated-staging"),
    staged_gate=False,
    downgrade_warning=False,
    pre_change=None,
    live_checkout="/home/amsyrzmi/CSAV2",
)

# Sizing and Postgres tuning for the production host. The repo file may set them; the host
# file may override them, so a resize needs no PR.
PROD_TUNING_KEYS = frozenset(
    {
        "CSA_PROD_WORKER_REPLICAS",
        "CSA_PROD_MEM_DB",
        "CSA_PROD_MEM_GATEWAY",
        "CSA_PROD_MEM_WORKER",
        "CSA_PROD_MEM_DELIVERY",
        "CSA_PROD_MEM_FRONTEND",
        "CSA_PROD_MEM_EMBEDDINGS",
        "CSA_PROD_MEM_CLOUDFLARED",
        "CSA_PROD_MEM_TRAINING_WORKER",
        "CSA_PROD_PG_SHARED_BUFFERS",
        "CSA_PROD_PG_EFFECTIVE_CACHE",
        "CSA_PROD_PG_WORK_MEM",
        "CSA_PROD_PG_MAINTENANCE_MEM",
        "CSA_PROD_PG_MAX_CONNECTIONS",
    }
)
_ORIGIN_KEYS = frozenset({"CSA_STAGING_CONSOLE_ORIGIN", "CSA_STAGING_HOOKS_ORIGIN"})
# Names the training worker's S3 key pair in compose.production.yml: unset it is the disabled
# placeholder; prod_s3_training once the owner made the key. Only these two: any other value could
# name another declared secret (the application key) as the training worker's.
TRAINING_KEY_SWITCH = "CSA_PROD_S3_TRAINING_KEY"
TRAINING_KEY_VALUES = ("prod_s3_training_disabled", "prod_s3_training")
# Video in the training library (gateway and training worker).
TRAINING_VIDEO_SWITCH = "CSA_STAGING_TRAINING_VIDEO"

# The instance's backup script, installed from deploy/production/backup.sh. Production runs it
# before anything changes; the rollout checks the installed copy matches the repository's.
BACKUP_SCRIPT = "/srv/csa-prod/backup/backup.sh"

# The interim production instance runs the staging stack under its own project, so the
# database service, name and user are staging's (container csa-prod-staging-db-1). The two
# origins are production hostnames, which the public repo does not commit: they come from the
# host file with the bucket and region, and the repo file holds the other six switches.
PRODUCTION = replace(
    STAGING,
    name="production",
    project="csa-prod",
    secret_dir=Path("/srv/csa-prod/secrets"),
    compose_files=(*STAGING.compose_files, "deploy/compose.production.yml"),
    profiles=("staging-approved", "pipeline-approved"),
    replicas={"worker": ("CSA_PROD_WORKER_REPLICAS", 2)},
    flags_file="deploy/production-rollout.env",
    required_keys=STAGING.required_keys - _ORIGIN_KEYS,
    optional_keys=PROD_TUNING_KEYS | STAGING.optional_keys | {TRAINING_KEY_SWITCH},
    optional_values={**STAGING.optional_values, TRAINING_KEY_SWITCH: TRAINING_KEY_VALUES},
    host_file=Path("/srv/csa-prod/host.env"),
    host_required_keys=frozenset({"CSA_PROD_S3_BUCKET", "CSA_PROD_S3_REGION"}) | _ORIGIN_KEYS,
    host_override_keys=PROD_TUNING_KEYS | {"CSA_PROD_DB_IMAGE"},
    staged_gate=True,
    downgrade_warning=True,
    pre_change=(BACKUP_SCRIPT,),
    live_checkout="/home/ubuntu/CSAV2",
)

_TARGETS: dict[str, Target] = {STAGING.name: STAGING, PRODUCTION.name: PRODUCTION}


def get_target(name: str) -> Target:
    try:
        return _TARGETS[name]
    except KeyError:
        raise ValueError(f"unknown target {name}") from None
