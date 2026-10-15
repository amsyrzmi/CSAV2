# CSA Console: Agentic Project Management Implementation Ledger

This ledger tracks the implementation, verification, and deployment state of each phase in the CSA Agentic Project Management roadmap.

## 1. Phase Implementation Ledger

| Phase | State | Commit / Branch | Validation Evidence | Manual Steps | Open Decisions |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **00 — Discovery** | `implemented_local` | `main` | Baseline tests passing (55/55); REPO_FINDINGS.md authored | Run `checks/inspect-server.sh` on homeserver & VPS; run `checks/inspect-db.sql` | DB host, runner host, Hermes identity & version, terminal scope |
| **01 — Admin Project Hub** | `implemented_local` | `main` | 69/69 backend tests passing (test_pm.py); Next.js build clean (npm run build); ESLint 0 errors | Review & apply additive schema migration | None |
| **02 — Task Board** | `implemented_local` | `main` | 86/86 backend tests passing (17 new in test_pm_tasks.py); Next.js build clean (npm run build); ESLint 0 errors; full live DB acceptance smoke-test passing | Applied 0004_pm_tasks.sql migration | None |
| **03 — Project Knowledge & Notes** | `implemented_local` | `main` | 102/102 backend tests passing (16 in test_pm_notes.py); Next.js build clean | Applied 0005_pm_notes.sql migration | None |
| **04 — LangGraph Planner** | `implemented_local` | `main` | 113/113 backend tests passing (11 in test_pm_planner.py); Next.js build clean; live PostgreSQL E2E smoke test verified | Applied 0006_pm_planner.sql migration; PM_AI_ENABLED=true; worker daemon (make pm-worker) | Deterministic stub by default; ready for Anthropic/OpenAI keys |
| **05 — Diagrams & Architecture** | `implemented_local` | `main` | 121/121 backend tests passing (8 in test_pm_diagrams.py); Next.js build clean (npm run build); ESLint 0 errors; live DB acceptance smoke test verified | Applied 0007_pm_diagrams.sql migration; PM_DIAGRAMS_ENABLED=true | None |
| **06 — Project Terminal** | `implemented_local` | `main` | 129/129 backend tests passing (8 in test_pm_terminal.py); Next.js build clean (npm run build); ESLint 0 errors; live DB and PTY lifecycle acceptance verified | Applied 0008_pm_terminal.sql migration; PM_TERMINAL_ENABLED=true; PM_RUNNER_ENABLED=true | None |
| **07 — Hermes Worker** | `implemented_local` | `main` | 136/136 backend tests passing (7 in test_pm_hermes.py); Next.js build clean (npm run build); ESLint 0 errors; canary memory isolation & bug reproduction verified | Applied 0009_pm_investigations.sql migration; PM_HERMES_ENABLED=true | None |
| **08 — Multi-Agent Workroom** | `implemented_local` | `main` | 145/145 backend tests passing (9 in test_pm_workroom.py); Next.js build clean (npm run build); ESLint 0 errors; bug-fixture handoff, budget exhaustion early halt, and branch conflict detection verified | Applied 0010_pm_workroom.sql migration; PM_WORKROOM_ENABLED=true | None |
| **09 — Client Portal** | `implemented_local` | working tree | Phase 09 API/UI and migration authored; no test/build or database migration execution in this turn | Review and apply `0011_pm_client_portal.sql`; enable `PM_ENABLED` and `PM_CLIENT_PORTAL_ENABLED`; select pilot tenant | Preserve existing tenant-wide client project access; client requests are comments only and never trigger work |
| **10 — Production Hardening** | `implemented_local` | working tree | Added liveness/readiness, admin queue/worker metrics, worker heartbeat, request/run correlation, PostgreSQL checkpoint option, idempotent proposal recovery, lease fencing, SSE/WS session revalidation, production HTTPS/cookie/origin checks, and ops runbook; no automated or staging checks run | Review/apply `0012_pm_operations.sql`; configure separate checkpoint DB; run backup/restore and fault drills in staging; verify deployment-specific ingress/runner/storage | Production topology, backup owners, on-call, capacity, action reconciliation and promotion gates must be confirmed |

*Allowed states: `pending`, `in_progress`, `implemented_local`, `verified_staging`, `enabled_production`, `blocked`.*

---

## 2. Feature Flags Status

Feature flags are separated from code completion to allow safe incremental shipping:

| Feature Flag | Default | Current Status | Description |
| :--- | :--- | :--- | :--- |
| `PM_ENABLED` | `False` | Enabled (Local) | Controls visibility of `/projects` and `/api/admin/pm/*` |
| `PM_AI_ENABLED` | `False` | Enabled (Local) | Controls LangGraph worker execution and proposal generation |
| `PM_DIAGRAMS_ENABLED` | `False` | Enabled (Local) | Controls Diagram Studio, architecture canvas, and SVG exports |
| `PM_RUNNER_ENABLED` | `False` | Enabled (Local) | Controls isolated workspace execution and testing |
| `PM_TERMINAL_ENABLED` | `False` | Enabled (Local) | Controls PTY creation and WebSocket terminal connections |
| `PM_HERMES_ENABLED` | `False` | Enabled (Local) | Controls dispatch to Hermes worker adapter |
| `PM_WORKROOM_ENABLED` | `False` | Enabled (Local) | Controls bounded multi-agent workroom workflows, version pinning, and interventions |
| `PM_CLIENT_PORTAL_ENABLED` | `False` | Disabled | Controls client-facing projection of shared project revisions |

---

## 3. n8n ➔ LangGraph Cutover Ledger (Customer Service Parallel Track)

| Function | n8n Retained | LangGraph Implemented | Contract Tests | Staging Smoke | Active Routing |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Chat send & poll** | Yes | Pending | Pending | Pending | `n8n` |
| **File ingest & delete** | Yes | Pending | Pending | Pending | `n8n` |
| **Voice fetch & transcription** | Yes | Pending | Pending | Pending | `n8n` |
| **Health / toggle behavior** | Yes | Pending | Pending | Pending | `n8n` |
| **Execution telemetry** | Yes | Pending | Pending | Pending | `n8n` |
| **External channels (TG/WA)** | Yes | Pending | Pending | Pending | Existing n8n |
