# Rules for the repository IDE AI

This file applies to the new CSA project-management module. Read actual repository AGENTS.md and build tooling too. Inspect before implementing: paths in the pack are suggested module boundaries based on documentary state, not a verified tree.

## Non-negotiable compatibility

1. Extend existing project identity and channels. Do not replace `app.projects`, mutate existing IDs, or reinterpret `setup/active/archived` as task statuses.
2. Preserve session guards, CSRF, audits, sanitization and the three DB engine boundaries. PM writes use app-owned DB access only.
3. Keep existing `/agents`, `/system`, `/settings/clients` and `/portal/*` working. Use `/project-agents` for the new global PM agent library.
4. New functionality starts admin-only behind `PM_ENABLED`. AI, diagram plugins, runner, Hermes and client sharing have separate flags and disabled-state responses.
5. Reuse `components/ui/kit.tsx`, AppShell, typography and current icy-blue tokens. Add accessibility and responsive behavior to new panels.
6. Use the repository's existing migration system. If none exists, add one using its ORM conventions and establish a baseline before changing schemas. Never apply schema edits through startup-time ad hoc DDL.
7. Do not broaden live domain write credentials, expose secrets to the browser, or give an agent application database credentials.
8. Write tests for changed permissions, state transitions, concurrency, recovery and integration contracts; avoid tests that merely restate implementation.
9. Pin new dependencies to a tested compatible set in the existing lockfiles. Inspect installed LangGraph and Hermes versions before copying API examples.
10. Finish only the requested phase. State what was implemented, what was verified, and any manual dependency still needed. Never claim server deployment, a real AI run, or production tests without evidence.

## Suggested boundaries

Backend: `app/modules/pm/` (projects, tasks, notes, artifacts, permissions), `app/modules/pm_ai/` (runs, definitions, events, approvals), `app/services/pm_tools/` (policy and capability handlers), and a worker entry point outside HTTP startup. Runner and Hermes adapters are private execution services.

Frontend: `/projects` and `/projects/[id]/*` in the existing admin route group; `/project-agents`; shared PM components in the existing component conventions. App Router route groups are not URL segments. Reuse existing portal project paths later rather than adding competing pages.

## Working procedure for every phase

- Inspect related routes, DB models/migrations, callers and tests.
- Record scope and relevant assumptions; choose minimal additive changes.
- Implement backend authority before UI affordances.
- Run targeted tests, lint/type checks, and applicable production build checks.
- Demonstrate the phase's acceptance scenario with seed data or local fixtures.
- Update STATE.md and the ledger, without rewriting unrelated state or declaring future features complete.
- Include a concise review note: changed files, behavior, migrations, flags, evidence, manual steps and remaining risks.

## Implementation ledger template

Create `docs/agentic-pm/IMPLEMENTATION_LOG.md` in the repository after discovery:

| Phase | State | Commit/branch | Validation evidence | Manual steps | Open decisions |
|---|---|---|---|---|---|
| 00 | pending | — | — | Server/DB checks | Hermes identity, runner host |

Allowed states: pending, in_progress, implemented_local, verified_staging, enabled_production, blocked. Record flags separately from implementation state. An implemented integration may remain disabled until its manual infrastructure prerequisites are fulfilled.

## Done means

The acceptance checks pass against the real schema or representative migration fixture; access checks occur on the backend; failure/loading/empty states work; every externally caused mutation is audited; the user-facing behavior matches the feature flag state; no known compatibility regression remains; and the handoff identifies any action that the operator must still perform.
