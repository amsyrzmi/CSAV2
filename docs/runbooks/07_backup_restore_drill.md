# Runbook 07 — Backup and Restore Drill

> **Status:** This older host-oriented procedure is superseded. Do not run its
> localhost commands, port numbers or schema example. See the current
> [release contract](../../deploy/RELEASE.md) and disposable acceptance below.

## Validated disposable restore evidence

`tests/test_packaged_console.py::test_packaged_synthetic_backup_restore` uses
synthetic data, `pg_dump --format=custom`, separate role definitions without
password hashes, and a fresh disposable Testcontainer target. It validates
migration ledger timestamps, synthetic tenant/identity/audit data, checkpoint
tables, role/RLS boundaries and packaged readiness. Run the focused acceptance
only under the repository's isolated Testcontainers configuration.

This proves a synthetic logical restore path only. It does **not** establish
production backup scheduling, encrypted-key/session recovery, uploads, WAL/PITR,
RPO/RTO or an operator-approved host restore command.
