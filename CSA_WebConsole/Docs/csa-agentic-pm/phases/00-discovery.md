# Phase 00 — Verify the real implementation

Read IMPLEMENTATION_RULES.md and the supplied STATE.md before this brief. This phase produces findings, not feature code. Do not connect to live systems without the operator's existing credentials and explicit environment selection.

## IDE AI work

1. Identify API/web roots, dependency locks, ORM and migration tooling, service startup, route groups, API client, testing fixtures and actual project models. Check repository AGENTS.md.
2. Find every reader/writer of projects and project channels, client counts, slug validation, portal project links and audit writes. Record paths and compatibility effects of introducing internal projects.
3. Confirm ID types, project constraints, client references, status handling and whether project deletion/archiving is supported. Document why cross-database client validation is a soft reference.
4. Inventory all n8n-specific dependencies: chat send/poll, document ingestion/deletion, voice dispatch/transcription, agent toggles, workflow health, executions page, and operational telemetry. Include current envelope schemas and idempotency behavior.
5. Identify cookie attributes, CSRF implementation and route guards. Verify whether WS/SSE would pass through the present ingress/rewrite stack. Record the current API bind and process count.
6. Run existing checks locally against test DBs; do not run write tests against live csa_db. Record actual results without copying STATE.md's historical numbers.
7. Inspect installed LangGraph packages and the Hermes project/version when available. If the runtime is elsewhere, prepare the exact diagnostic request rather than inventing its API.

## Outputs

Create `docs/agentic-pm/REPO_FINDINGS.md`, `IMPLEMENTATION_LOG.md` and a small `DECISIONS.md`. Findings should map proposed module boundaries to actual paths, list current DB schema evidence, name the migration strategy and distinguish verified facts, assumptions and unavailable server information. Include the minimal proposed phase 01 change and compatibility test list.

Decisions needing operator input: whether the app DB moves or remains where it is; runner on VPS/dedicated VM/homeserver; actual Hermes distribution and version; whether “local terminal” means laptop or hosted project runner; repository authentication method; chosen model provider and a starting budget.

## Operator work

Run checks/inspect-repo.sh locally and checks/inspect-server.sh on the homeserver; run the server check on the VPS too if Hermes is there. Use checks/inspect-db.sql through your existing read-only PostgreSQL connection. See MANUAL_RUNBOOK.md. Review outputs before sharing; hostname, paths and topology can be sensitive even without secret values.

## Acceptance

A reviewer can locate the real project/auth/migration code, understand every dependency affected by an internal project or n8n migration, see valid baseline checks, and identify infrastructure gaps. No new production schema or feature is enabled. Continue local phase 01 work when schema/auth facts are established; runner/Hermes unknowns need not block the basic project hub.
