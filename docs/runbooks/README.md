# Operational runbooks

These are operator references, not standing authorization to change a database,
credential, tenant or live service. Read [AGENTS.md](../../AGENTS.md),
[current progress](../PROGRESS.md) and the task-specific handoff first. Confirm
the exact target/environment and obtain separate approval before any persistent
operation. Never use production data/secrets in tests.

The SQL snippets in these documents are illustrative historical procedures.
They are not all wired to an authenticated CLI or verified against every current
deployment role/schema. Do not copy/paste a write statement into a live database.
Prefer the parameterized, tenant-scoped procedures under `csa/ops/runbooks.py`
and `db/sql/ops/` where their tested contract applies; these are code primitives,
not necessarily an operator-facing command or authorization workflow. Read the
implementation and its tests before execution.

| Document | Purpose | Current qualification |
|---|---|---|
| [01 — replay failed turn](./01_replay_failed_turn.md) | Requeue a failed ingress event. | Programmatic `requeue_turn` exists; verify event state and side effects before retry. |
| [02 — DLQ](./02_drain_inspect_dlq.md) | Inspect and re-drive dead-letter work. | `inspect_dlq`/`redrive_dlq` exist; caller supplies transaction/tenant context. Old multi-statement SQL is not equivalent or approved. |
| [03 — circuit breaker](./03_circuit_breaker_reset.md) | Inspect/reset a tenant capability breaker. | Parameterized inspect/reset helpers exist. |
| [04 — Jev fallback](./04_jev_outage_fallback.md) | Change decision mode. | `force_decide_mode` exists; shadow waits for both classifiers and is not non-blocking. |
| [05 — credential rotation](./05_rotate_credentials.md) | Rotate channel credentials. | No generic safe rotation CLI/procedure is established by this runbook. |
| [06 — tenant mode](./06_disable_client_force_suggest.md) | Change deployment mode or suspend a tenant. | `set_deployment_mode` exists; not an instantaneous stop for already in-flight work. |
| [07 — backup/restore](./07_backup_restore_drill.md) | Recovery drill. | Disposable synthetic restore acceptance exists; production backup automation/RPO/RTO do not. |
| [08 — approvals](./08_approve_expire_stuck_approvals.md) | Expire overdue staff approvals. | Parameterized helper exists; caller supplies transaction/tenant context. Scheduled `pending_actions_expiry` does not expire approvals. |

For validated local setup and test commands see the [development runbook](../RUNBOOK.md).
For build-only artifacts and the inert staging proposal see the [release contract](../../deploy/RELEASE.md).
