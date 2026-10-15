# Operator steps: checks, tonight's migration and later execution services

This pack plans your existing application; it does not deploy it. Exact Compose names, DB service names, API bind addresses and server paths must come from phase 00. Commands below are templates or provided diagnostics, not guesses about your installation.

## 1. Collect context now

Run inside your checkout after extracting the pack:

```bash
bash docs/agentic-pm/checks/inspect-repo.sh . > repo-context.txt
```

On the homeserver, copy only the diagnostic script there and run it:

```bash
bash inspect-server.sh > homeserver-context.txt
```

If Hermes is still on the VPS, run the same script there and keep that output separately. These scripts do not install, restart, mutate DB state, print environment values or inspect private memory. They may reveal hostnames, IPs, repo paths and container names; review those before sharing. No sudo is required; inaccessible checks are reported as unavailable.

For PostgreSQL use your existing libpq service or a read-only role. Replace this service name with your actual configured service; the script is read-only regardless:

```bash
psql 'service=YOUR_CONSOLE_READONLY_SERVICE' -X -v ON_ERROR_STOP=1 \
  -f docs/agentic-pm/checks/inspect-db.sql > console-schema.txt

psql 'service=YOUR_DOMAIN_READONLY_SERVICE' -X -v ON_ERROR_STOP=1 \
  -f docs/agentic-pm/checks/inspect-db.sql > domain-schema.txt
```

If using Docker locally instead, substitute the actual container/database/role names:

```bash
docker exec -i YOUR_PG_CONTAINER psql -X -U YOUR_READONLY_ROLE -d YOUR_DATABASE \
  -v ON_ERROR_STOP=1 < docs/agentic-pm/checks/inspect-db.sql > db-schema.txt
```

Use a protected `.pgpass`/service configuration or existing secure credential flow; do not paste password-bearing connection URLs into chat or command arguments. SQL output is schema/constraint metadata, not client rows, messages or password fields. The SQL checks both app and relevant domain tables when present.

Return the reviewed findings plus these answers:

1. Is Hermes Nous Research Hermes Agent? Which commit/version and install method? Does its installed help expose a supported headless interface?
2. Will console_db move to the homeserver, and will services use Docker Compose or another supervisor?
3. Does “local terminal” mean your laptop, or the project's shell on the server? The pack defaults to a hosted isolated project runner.
4. Where are project repositories, and what read-only authentication method should the runner use?
5. Which model/provider and starting per-run budget should the planner use? What RAM/CPU capacity is available?

## 2. Tonight: move the current console first

1. Inventory current app/domain DB locations and every persisted directory: FAQ files, temporary/active voice files, application configuration and existing n8n storage. Back up relevant DBs and files through your normal backup method. Verify a restore into a disposable DB; never overwrite the live DB for a restore test.
2. Build the current web/API from pinned lockfiles and verify existing tests before deploying a new version. Record the old image/commit, provider mode and configuration as the rollback target.
3. If console_db is moving, quiesce app writes for final consistent DB/file transfer. Restore into a new target and verify migrations, sessions, projects, channel bindings, counts and file references. Keep the old target intact until cutover verification; avoid two independently writable copies.
4. Put web/API/DB on the chosen private network and HTTPS ingress. Replace developer tunnel assumptions deliberately. Container `127.0.0.1` means that container, not nexusserver or another service. Change API upstreams to approved service DNS/private addresses and container ports, not laptop forward ports 5433/5679/8001 by habit.
5. Set the new CONSOLE_ORIGIN, secure cookie settings, allowed origins and trusted ingress configuration. Update backend/Next upstream configuration consistently. Keep DB, coordinator, renderer, worker and runner ports private.
6. Your voice listener is currently launched inside API lifespan on :8090. Multiple API workers/reload can collide on that port. Before production use either one API process with the current listener or split voice into a separately supervised service and update health/startup tests. Do not blindly start multiple Uvicorn workers.
7. Point VOICE_AUDIO_BASE_URL to the new privately reachable voice service and verify signed URL expiry/tamper handling. Update FAQ and voice storage paths/volumes; changing host can otherwise lose file availability while DB metadata remains.
8. Smoke-test login/forced password change/invites, admin monitoring, client projects/channels, toggles on sandbox clients, chat send/poll/history/delete, FAQ upload/download/delete and voice transcription. Retain the old provider path until each enabled replacement passes.

## 3. Tonight: migrate n8n deliberately

Do not point every existing n8n URL to a generic LangGraph endpoint. Introduce a provider interface and preserve the external console contracts first. Default existing production routes to n8n until each replacement is verified; new PM runs can use LangGraph independently from the beginning.

| Existing dependency in STATE.md | LangGraph transition requirement | Fallback |
|---|---|---|
| Chat send and poll webhooks | Same trace/idempotency semantics, durable turn status and attachments | n8n chat provider |
| File ingestion/deletion webhook | Storage/ingestion contract and confirmed delete behavior | Existing n8n file workflow |
| Voice envelope/audio fetch/transcription | Reachable signed audio fetch, transcriber and status handling | Existing n8n voice path |
| `workflow_id`, workflow_off health state | Explicit runtime/provider-aware health adapter | Keep n8n monitoring for n8n agents |
| `/system` n8n executions | New run telemetry or provider-aware tab without broken deep links | Existing n8n execution view |
| Domain agent_runs and conversations/messages | Decide writer contract/schema; preserve authorized existing readers | Current production writers |
| Agent enabled flags | Both runtime paths must honor provider/client/circuit flags | Existing toggle service |

Proposed config (new settings to implement, not existing keys): separate provider modes for chat, files and voice (`n8n`/`langgraph`) and independent PM_AI_ENABLED. If only chat is ported tonight, keep files/voice explicitly on n8n. A full n8n retirement requires replacements for every production workflow, including external channels outside the web console; this pack is not evidence that those are ready.

Dispatch each request to exactly one active provider. Shadow only non-mutating dry-runs with suitable sanitized data. Never mirror production messages/files/actions to both providers as live writes. Recover in-flight turns before switching; keep trace/status compatibility for pending browser polls.

Promote a sandbox tenant first. Validate contract tests and a real end-to-end turn. For rollback, switch new dispatches back to n8n and route/reconcile in-flight LangGraph runs by their original provider; do not submit them again as new n8n actions. Keep additive app migrations and artifacts while disabling the new feature. No domain schema/grant expansion occurs as a side effect of adding PM.

## 4. Provision later PM infrastructure

- Graph DB: separate logical database/role for library checkpoint tables. Use verified setup/migration steps for the installed package. App worker credentials are limited to required PM tables; model tools never receive them.
- Worker: supervise separately, configure app queue/checkpoint connections, heartbeat, budgets and shutdown. Verify restart/resume before enabling real jobs.
- Artifacts: private persistent volume or object store, authorized downloads and consistent DB/file backup. Notes can initially remain DB-only.
- Renderer: pinned local Mermaid/PlantUML export engine, approved vendored assets, no arbitrary network/file reads, resource/output limits.
- Runner: preferably dedicated VM/host, fixed sandbox templates, no production mounts/credentials/Docker socket, controlled network access, per-project worktrees and resource caps.
- Repository access: approved repository list and short-lived/read-only credentials first. Publishing credentials are a separate later capability.

## 5. Move Hermes without mixing its personal and PM identities

1. Confirm runtime source/version, install method and the real data location from its installed configuration/docs. Do not assume ~/.hermes if you configured another path.
2. Make a private encrypted backup. Runtime homes may include credentials, conversations, memories and cron/session data. Do not attach that backup or its contents to this planning conversation.
3. Install/pin the equivalent runtime on the target server. Stage identity/state transfer using your secure transfer mechanism and restrictive file ownership/permissions. Verify provider access privately.
4. Stop the old runtime before the final state copy. Do not run two writers against a shared/copied active profile. Check cron/gateway jobs so the old server does not keep responding after the move.
5. Start and test the personal runtime on the target privately; retain an offline rollback copy. Avoid modifying it to serve PM jobs.
6. Create fresh PM runtime homes and minimal credentials separately. Seed only reviewed generic identity/skills. Register these through the new adapter and isolated runner.
7. Test two projects with distinct canary facts, deadline/cancellation, result validation and network isolation before enabling code execution.

## 6. Feature promotion sequence

Enable PM project hub → manual board/notes → internal LangGraph planner → diagram renderer → isolated admin terminal → scoped Hermes investigator → approved sandbox patches → bounded multi-agent runs → reviewed client sharing. Only enable a phase when its backend and manual infrastructure checks pass.
