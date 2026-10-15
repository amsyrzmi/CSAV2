# Phase 10 operations and rollout

This checkout has a development Compose file only. It is not a production manifest. Production host names, storage paths, ingress, capacity, credentials, and service supervisor have not been verified, so this runbook uses PostgreSQL service names and protected credential files instead of guessing deployment details.

## Local changes in this phase

- API liveness (`/api/health/live`) is process-only. Readiness (`/api/health/ready`) requires `app_db` and `domain_db`; in production with PM AI enabled it also requires the configured checkpoint database. Optional feature flags and worker liveness are reported separately so a worker outage does not take manual project boards offline.
- Admin aggregate telemetry is available at `/api/admin/ops/pm`. It reports queued/leased counts, oldest queue age, and worker process heartbeats without project content or tenant IDs.
- The worker upserts its heartbeat while idle or working. Runs carry a UUID correlation ID in `X-Request-ID`, the persisted run row, the accepted response and worker lifecycle logs.
- Resumable SSE streams recheck session revocation during delivery. Terminal tickets bind to the authenticated app session, and active WebSockets recheck session revocation, user status and terminal expiry.
- Production settings require an HTTPS `CONSOLE_ORIGIN`, `COOKIE_SECURE=true`, and `PM_CHECKPOINT_DATABASE_URL` when PM AI is enabled. Unsafe or missing `Origin` headers are rejected for production state-changing HTTP requests.
- The worker uses LangGraph's supported `PostgresSaver` with its own database URL and calls the library's `setup()` routine. Configure a dedicated logical database and role; do not point this at `csa_db`. If the URL is absent in development, the worker intentionally uses `MemorySaver` and logs that restart recovery is unavailable. Do not enable PM AI in production in that mode.
- The current worker enforces its lease owner/fencing token before graph writes and completion. A stale worker cannot publish a proposal or overwrite the active worker's run result.

## Migration and configuration

Review and apply `csa-console/api/sql/0012_pm_operations.sql` to `console_db` before starting the updated worker. Keep the PM AI flag off until this is applied and the worker appears healthy in `/api/admin/ops/pm`.

Set these values through the deployment's protected secret/configuration mechanism, not in a committed file:

```dotenv
DEPLOYMENT_ENVIRONMENT=production
CONSOLE_ORIGIN=https://console.example.invalid
COOKIE_SECURE=true
PM_CHECKPOINT_DATABASE_URL=postgresql+psycopg://<worker-role>:<secret>@<private-db-host>:5432/<graph-db>
```

The graph role should have access only to its checkpoint database. The app worker still needs its separately scoped app database URL for the queue and project records. Keep the graph database, renderer, runner, coordinator and PTY ports private.

## Backup and restore drill

Run backup jobs from the operator host using protected libpq service entries or the deployment's secret manager. Do not pass a password-bearing URL on the command line. Keep encrypted backups off-host and record backup timestamp, app version, migration revision and artifact manifest.

Back up each independently persisted store:

1. `console_db` (projects, tasks, notes/revisions, artifacts metadata, actions, approvals, events, audit log and worker queue).
2. The dedicated graph/checkpoint database.
3. Private artifact files/object storage, if any are configured outside PostgreSQL.
4. Only explicitly selected project Hermes homes, if project-specific durable memory is enabled. Never mix a personal Hermes home into a PM backup.
5. Required FAQ/voice/application-owned files according to the existing CSA runbook.

Example for a configured `pg_service.conf` entry; write output to an encrypted backup directory with restrictive permissions:

```sh
umask 077
pg_dump 'service=console_backup' --format=custom --no-owner --file=/secure-backups/console_db.dump
pg_dump 'service=graph_backup' --format=custom --no-owner --file=/secure-backups/graph_db.dump
```

Restore only into newly created disposable databases, never over production:

```sh
createdb --maintenance-db='service=restore_admin' console_restore_check
pg_restore --no-owner --dbname='service=restore_admin dbname=console_restore_check' /secure-backups/console_db.dump
```

Repeat for the graph DB and restore file/object artifacts to an isolated location. Verify migration state, project ownership boundaries, note and diagram revisions, active approvals/actions, audit log counts, run/job links, and artifact hashes. Confirm a restored checkpoint does not cause a completed side effect to repeat: reconcile action IDs/provider receipts before re-enabling workers. Drop disposable restore databases only after recording evidence and confirming the target names; never use a production service for this cleanup.

## Retention policy

Initial policy for operator review and enforcement before broad production use:

- Audit log: retain immutably under the separate audit retention policy; do not include it in routine PM cleanup.
- Project notes, accepted artifacts, action receipts and client publication history: retain for the life of the project unless a documented customer/data-retention obligation requires a reviewed deletion workflow.
- Run events and terminal session metadata: retain 180 days after a terminal run/session, subject to legal/customer requirements. Do not retain raw terminal scrollback beyond the configured short reconnect window.
- Graph checkpoints: prune 90 days after terminal runs only through the installed LangGraph checkpointer's supported thread deletion/pruning APIs. Exclude active runs, pending approvals and any action needing reconciliation.
- Project-specific Hermes homes: retain only while the assignment/project is active plus a 30-day recovery window; delete through the adapter after checking for active jobs. Personal Hermes state is outside this policy.

These durations are initial defaults, not proof of a configured cleanup job. Record the responsible operator and actual cleanup mechanism before production enablement.

## Release, failure and rollback drills

Before a staging promotion, capture the current commit/image, applied migrations, feature flags, provider routes and database backup IDs. Exercise at least:

- Worker stop during a job, lease expiry, second worker takeover, stale fencing rejection and idempotent duplicate enqueue.
- Lost/ambiguous Hermes or runner result, model throttling, graph DB outage and app DB outage. Reconcile by stable action/run IDs; never blindly resubmit an unknown external action.
- Runner network loss and malicious repository instructions attempting cross-project reads, secret disclosure or policy changes.
- Stale approval after action/base commit changes, expired session during SSE, forged WebSocket Origin and session revocation during a terminal session.
- Artifact volume full, renderer unavailable, and client snapshot unshare/revision access after revocation.
- HTTPS cookie/Origin enforcement and ingress SSE/WS idle timeouts on the real staging ingress.

Rollback should disable the affected feature flag or route new requests to the prior image/provider first. Keep additive migrations and data in place. Do not treat a destructive schema downgrade or replay of in-flight actions as an automatic rollback.

## Promotion gates

Promote through internal admin projects → one client-linked admin project → one sandbox client pilot → wider client rollout. Keep execution, repository write and deployment capabilities disabled until runner boundaries, approval fencing, credentials and recovery have been independently verified. Record rollback target and on-call owner before each gate.

## Not yet demonstrated

The local implementation does not establish a production manifest, backup schedule, restore evidence, checkpoint DB provisioning, artifact-store coverage, on-call ownership, ingress settings, measured capacity, or Hermes/runner network isolation. `/api/health/ready` is a dependency probe, not a full staging acceptance result. Run the manual drills on the target staging services before marking Phase 10 `verified_staging`.
