# Phase 10 — Production hardening and rollout

Prerequisite: only features already implemented and validated are candidates for enablement. Earlier phases already enforce permissions, durable jobs and sandbox constraints; this phase proves the whole deployed system under faults and load.

## IDE AI work

1. Finalize service manifests/configuration for web, API, worker, graph DB connection, private artifact storage, renderer, Hermes adapter and runner. Reuse existing deployment conventions; no new orchestration platform is required.
2. Split liveness from readiness. Readiness reports required dependencies; optional Hermes/renderer outages appear as degraded features, not total console failure. Define worker heartbeat, queue latency and reconciliation metrics.
3. Add trace correlation across API/run/action/provider IDs, structured redacted logs, useful alerts and budget visibility. Avoid model prompts, credentials and raw tenant data in labels/logs.
4. Test release migrations and backups/restores for app data, artifacts, checkpoint state and selected project-specific Hermes homes. Restoring checkpoints does not mean repeating completed actions; reconcile external receipts after recovery.
5. Define retention for events, terminal scrollback, artifacts, memory and checkpoints with ownership/active-run exclusions. Use supported library pruning behavior for checkpoint tables. Audit retention separately so cleanup cannot undermine its immutability.
6. Run failure drills: worker killed during mutation, lease takeover, duplicate request, lost Hermes response, runner network loss, expired session on SSE/WS, model throttling, graph DB outage, artifact disk full, stale approval and malicious repository content.
7. Verify production HTTPS cookie/Origin configuration, ingress SSE/WS timeouts, private port exposure and actual runner network boundaries. Load-test the measured concurrency cap, not invented customer throughput claims.
8. Add per-feature rollout flags, service-version rollback steps and an operator runbook. Prefer disable flag/revert binary first; destructive migration downgrade is not an automatic rollback strategy.

## Acceptance

A complete staging workflow survives a worker restart and SSE reconnect without duplicate tasks/actions. Unknown external outcomes become needs_reconciliation. Restore a representative backup and validate project ownership, note/diagram revisions and approved action ledger. Manual PM works during AI outage. Demonstrate cancellation, budget stops, stale-lease rejection and clean client projections. Existing auth/chat/FAQ/voice/agent-monitoring checks still pass for the enabled provider paths.

## Operator work

Promote progressively: internal admin projects → one client-linked admin project → one client pilot → broader rollout. Keep repository/deployment write capabilities disabled until their separate approval and credential scopes are verified. Record rollback target and on-call ownership before enabling automated execution. Reassess defaults from actual resource/cost measurements.
