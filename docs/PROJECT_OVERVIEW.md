# Project orientation, architecture and workflow

Status: repository/source overview, updated 2026-10-20. This describes the
implemented code and the recorded evidence linked below; it is not proof that
services are running or deployed.

## Start here

| Need | Read |
|---|---|
| Rules and safe commands | [AGENTS.md](../AGENTS.md), then [README](../README.md) |
| Words on screen | [Writing guide](./WRITING_GUIDE.md) |
| Current implementation status and validation | [PROGRESS.md](./PROGRESS.md) |
| Continue the current bounded task | [Active release handoff](archive/handoffs/2026-10-19-domain-release-next-session.md) |
| Full architecture, trust boundaries and limitations | [Architecture reference](./ARCHITECTURE.md) |
| Runtime request flow | [Architecture summary](./ARCHITECTURE.md) |
| Console HTTP contract | [CONSOLE_API.md](./CONSOLE_API.md) and [OpenAPI snapshot](./console_openapi.json) |
| Phase requirements | [LG00–LG15 plans](./langgraph-rebuild/) and the linked milestone plan |
| Local development operations | [Development runbook](./RUNBOOK.md) |
| Build-only release and staged deployment gates | [Release contract](../deploy/RELEASE.md) |

Do not load every historical handoff or phase plan as general context. Start
with the active handoff and read only the plan and tests relevant to the requested
change. Older handoffs and evaluation reports preserve dated evidence, not current
service state or authorization.

## What CSA is

CSA is a multi-tenant customer-service runtime. It accepts supported channel
events, persists work, coordinates bounded domain capabilities, records a reply
or human-review decision, and delivers through an outbox. An authenticated Next.js
console provides tenant-scoped administration and staff workflows. PostgreSQL is
the system of record; language models propose classifications, plans and wording,
while application/database rules determine identity, permissions and business
state.

## End-to-end architecture and workflow

```text
Customer channel
  -> gateway: authenticate source, normalize and deduplicate event
  -> PostgreSQL ingress queue
  -> worker: ordered claim + per-conversation lock
  -> LangGraph coordinator: guard, route, plan, bounded spokes, verify and gate
  -> transactional records / approval / human handoff + durable outbox intent
  -> delivery process: provider send, retry and recorded attempt

Staff browser
  -> adopted Next.js application
  -> same-origin /console/v1 transport
  -> FastAPI gateway: session, CSRF, tenant and role checks
  -> PostgreSQL identity/domain stores and audited service operations
```

These are separate process responsibilities: the gateway accepts HTTP and channel
callbacks; the worker processes customer turns; delivery sends due outbound work
and staff notifications. LangGraph coordination is confined to `csa/graph/`.
The web console is not a public catch-all proxy to the gateway. The mounted legacy
marketplace routes are not substitutes for the unfinished M6B verifier flow.

Key safety boundaries:

- Session-derived identity, active membership, role checks and tenant-scoped SQL
  govern human console operations. Browser-supplied actor/tenant headers are not
  authority.
- PostgreSQL migrations and grants/RLS enforce data boundaries; schema changes
  are additive and ordered. Structural child tables may inherit tenant scope
  through a constrained parent rather than a direct `client_id`.
- A model result is not a persisted business outcome. Mutations, approval, audit,
  replay and external delivery have explicit application contracts.
- External provider calls, credentials and live seller eligibility are separate
  gates. Synthetic adapter tests do not constitute a live integration.
- The inbox/outbox and graph are durable, but external delivery is not exactly-once.
  See [architecture limitations](./PROJECT_OVERVIEW.md#current-status-and-release-boundary).

## Current status and release boundary

- The adopted runtime is Python 3.12, FastAPI, async psycopg 3, LangGraph and
  PostgreSQL; the adopted UI is the Next.js application under
  `CSA_WebConsole/csa-console/web/`.
- The authenticated internal staff workspace assistant (floating dock on every tab)
  has distinct owner-private conversations, deterministic page guidance, a tenant-scoped
  local order read and, behind `WORKSPACE_ASSISTANT_ENABLED` (default off), a planner
  that proposes one typed action per message. Every mutation (email export, training
  submit, inbox decision) is a proposal the user confirms on a card; a basic company
  directory and the caller's manager lookup serve it. It is separate from the customer
  sandbox and has no voice integration. See [CONSOLE_API.md](./CONSOLE_API.md#workspace-assistant).
- M5 Telegram staff inbox/reply approval has historical guarded-dev acceptance.
  M6 remains incomplete: local business reads are not marketplace synchronization
  or authoritative stock management.
- M6B verifier/completion pieces are synthetic/offline foundations and are not
  callback/runtime registered. No real app registry, provider credential
  resolution or live seller authorization is established.
- Source currently expects schema 0460. The last approved persistent-dev schema
  was recorded as 0300; that record is not a fresh probe or migration approval.
- CI/package and exact hosted-artifact disposable acceptance passed for the
  reviewed build. See [current progress](./PROGRESS.md) for run IDs, exact evidence
  and the current PR/CI state. This is not public browser/ingress, persistent
  staging or production acceptance.
- The future `console.fourstacks.my` selection preserves the existing
  `fourstacks.my`/`www` landing page. No DNS, tunnel, provider, persistent database
  or service change is authorized by this documentation.

## Service packages

Capabilities are sold and managed as service packages defined in
`csa/console/packages.py`. A tenant's active packages decide which console pages
and APIs are available (`package_inactive` otherwise); the per-agent switches
still enforce runtime behaviour. Add-ons require `customer_support`.
See [console API](./CONSOLE_API.md#services-and-packages).

| Package | Kind | Capabilities | Console surface | Sellable |
|---|---|---|---|---|
| `customer_support` | base | `kb_agent`, `ticket_agent`, `escalation_agent`, `order_approval` | Inbox, Knowledge base | yes |
| `orders_products` | add-on | `order_agent`, `invoice_agent` | Business | yes |
| `subscriptions` | add-on | `subscription_agent` | Subscriptions (plans, customers' subscriptions, request queue) | yes |
| `bookings` | add-on | `booking_agent` | Bookings (specialised) | yes |
| `marketplaces` | add-on | `ecommerce_agent` | Business, E-commerce (specialised) | no |
| `e_invoicing` | add-on | `lhdn_agent` | LHDN (specialised) | no |
| `hr` | standalone | none; covers `hr_agent_settings` modules | People & HR | yes |
| `staff_email` | standalone | none; covers `email_settings` | Email | yes |

## Repository map

| Path | Responsibility and status |
|---|---|
| `apps/gateway/` | FastAPI lifecycle, channel/console routes, health and readiness. |
| `apps/worker/` | Durable conversation claims and coordinator execution. |
| `apps/delivery/` | Outbox delivery and staff notification sender. |
| `apps/telegram_poller/` | Explicitly guarded development polling entry point. |
| `csa/contracts/` | Typed envelopes, graph state, plans, facts, callbacks and results. |
| `csa/core/` | Settings, pools/transactions, tenancy, encryption, privacy, logging and tracing. |
| `csa/channels/` | Channel protocols, Telegram ingress/transport and marketplace groundwork. |
| `csa/turn/` | Runtime dependency construction and application wiring. |
| `csa/graph/` | LangGraph coordinator, policies, event projection and checkpoint support. |
| `csa/decide/` | Classifiers, planners, composers, reviewers and model-provider clients. |
| `csa/agents/` | Bounded domain spokes, agent protocol and runner. |
| `csa/console/` | Console identity, authorization, services and route contracts. |
| `csa/jobs/`, `csa/ops/`, `csa/approval/`, `csa/staff/`, `csa/notify/` | Scheduled/recovery operations, approvals, staff control and notifications. |
| `db/schema_baseline.sql` | Reviewed inherited schema baseline; not current schema by itself. |
| `db/migrations/` | Ordered additive schema, role, policy and index changes. |
| `db/sql/` | Parameterized SQL organized by service/domain. |
| `db/seed/` | Synthetic sandbox seed data. |
| `scripts/` | Migration/setup/bootstrap/export and other bounded operator commands. |
| `tests/` | Unit, contract, Testcontainers, process, golden, frontend and acceptance tests. |
| `CSA_WebConsole/csa-console/web/` | Adopted Next.js console and isolated build/verification scripts. |
| `CSA_WebConsole/csa-console/STATE.md` | Historical copied-console status snapshot; not active runtime guidance. |
| `ui-guide/` | Visual references/design guidance, not application source or runtime behavior. |
| `deploy/` | Dockerfiles, development/legacy manifests, and inert gated staging/release proposals. |
| `.github/workflows/` | Hosted CI and build-only artifact workflow; promotion/deployment is disabled. |
| `.agents/skills/` | Task-specific engineering workflows; load the relevant skill before work. |
| `docs/langgraph-rebuild/` | LG phase plans: intent, acceptance criteria and staged roadmap. |
| `docs/handoffs/` | Dated decisions, milestone evidence and continuation records. Read the current one only. |
| `docs/evals/` | Dated synthetic evaluation/load reports; not live provider guarantees. |
| `docs/runbooks/` | Operator references; read its status index before using any procedure. |

The former `CSA_WebConsole/Docs/` tree contained a deferred project-management
proposal, a copied pre-migration console source snapshot and a large n8n-era
workflow document. These were not maintained as CSAV2 source or instructions and
were removed on 2026-10-20. The adopted frontend lives under
`CSA_WebConsole/csa-console/web/`; design references remain under `ui-guide/`.

## Documentation authority and maintenance

Resolve disagreements in this order:

1. Executable source, ordered migrations, tests and generated API schema establish
   what is implemented and its observable contract.
2. `docs/PROGRESS.md` and the active handoff establish dated validation and
   current continuation state.
3. `docs/CONSOLE_API.md`, `deploy/RELEASE.md` and maintained runbooks describe
   supported interface and operator boundaries.
4. LG phase plans define intended scope and acceptance criteria, not shipped status.
5. Dated handoffs/evals/reviews preserve historical evidence. Deferred UI/PM specs
   are references only unless a current task explicitly adopts them.

For every implementation slice: read `AGENTS.md`, current progress, the active
handoff and relevant phase criteria; inspect source and tests; make the smallest
complete change; run focused isolated checks and repository-required validation;
record exact outcomes and unmet gates. Keep failures, skips and timing thresholds
visible. Do not use production data/secrets or persistent dev/production services
for tests. Use disposable Testcontainers; destructive persistent reset, migration,
external provider activity, deployment and infrastructure changes need explicit
authorization.

For code/database changes use repository guidance for `make lint test` and the
disposable-only reset test. For docs-only edits, validate changed links/fences and
`git diff --check`; do not start services to validate prose.
