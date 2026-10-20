---
name: csa-console-feature
description: Implement or review an authenticated CSA console feature across FastAPI contracts, tenant authorization, SQL, OpenAPI and the adopted Next.js frontend. Use for csa/console or CSA_WebConsole feature work.
---

# Build a CSA console feature

Read [AGENTS.md](../../../AGENTS.md), [LG15](../../../docs/langgraph-rebuild/LG15_console_turnover.md),
the relevant M-stage in the [console migration plan](../../../docs/handoffs/2026-10-15-console-source-migration-plan.md),
and [CONSOLE_API.md](../../../docs/CONSOLE_API.md). For frontend edits also read the
[frontend AGENTS.md](../../../CSA_WebConsole/csa-console/web/AGENTS.md) and installed
Next.js documentation required there.

## Carry one contract through the stack

- Define Pydantic request and public response models. Public projections must omit
  secrets, filesystem paths, internal metadata and provider payloads.
- Derive human identity, tenant and role from the verified session. Platform admins
  select a tenant explicitly; tenant members cannot choose another tenant. Never accept
  browser actor/role headers. Keep machine access read-only and allowlisted.
- Require CSRF for cookie-authenticated mutations and enforce the same permission at the
  service boundary. UI visibility is not authorization.
- Put parameterized tenant SQL in `db/sql/` and execute it inside the existing
  transaction-local tenant context. Preserve role-specific read and mutation behavior.
- Model queued, processing, uncertain, delivered, suppressed, expired and failed states
  truthfully where they apply. Reconnect should inspect persisted state before repeating
  an uncertain mutation; explicit retries reuse the same idempotency key and payload.
- Update server transport, adopted UI and error handling together. Do not expose deferred
  reference pages or legacy API routes as implemented features.

If the public API changes, regenerate `docs/console_openapi.json` with
`NO_LOCAL_ENV=1 ENV=ci uv run python -m scripts.export_console_openapi` and review the
contract diff. Add backend tests for anonymous, platform-admin, tenant-admin, staff and
reader boundaries relevant to the feature, including cross-tenant attempts.

Use the existing frontend transport/state tests and run
`npm --prefix CSA_WebConsole/csa-console/web run check:isolated`. This check must retain
its reviewed source allowlist and exclusion of environment files, nested Git, backups and
the legacy API. A successful isolated build does not establish authenticated browser or
live provider acceptance.

Use `$csa-db-change` for schema work and `$csa-verify-change` for final verification.
Starting or restarting dev services, applying persistent migrations and performing a
browser rollout are separate actions governed by the current session's authorization.
