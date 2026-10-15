# Handoff: Wave 2/3 provider-neutral preparation (2026-10-15)

> Console source has since been supplied at `CSA_WebConsole/`. For console work use
> [the source-based migration plan](./2026-10-15-console-source-migration-plan.md);
> use its current M2 continuation prompt for console work.

Read [LG13](../langgraph-rebuild/LG13_agent_catalog_and_console.md),
[LG15](../langgraph-rebuild/LG15_console_turnover.md), and
[the current console migration plan](./2026-10-15-console-source-migration-plan.md)
for the larger migration.

## Agreed scope

The user explicitly selected:
1. Provider-neutral subscription preparation; choose the payment provider later.
2. Safe backend features for all waves now: validated persisted drafts/requests and local
   reads, with no live provider execution. Console source is not needed for this slice.

Do not describe Wave 2/3 as fully complete or production-enabled.

## Implemented

- `csa/agents/staged/models.py`: strict, bounded Pydantic inputs. Booking requires explicit
  aware times and matching IANA zone offsets (including DST checks). Invoice line values
  use Decimal, explicit tax rates, per-line 2dp half-up rounding, and persisted-amount bounds.
  Email reply headers reject controls/multiple-recipient syntax. Research accepts a bounded
  list of HTTPS DNS-name URLs; this is only request validation, not an SSRF-safe fetcher.
- `csa/agents/staged/service.py`:
  - `BookingAgent`: local list/status, prepare book/reschedule/cancel requests.
  - `SubscriptionAgent`: active plans, contact-scoped list/status, prepare pause/cancel/change.
  - Both are wired into `CAPABILITIES`, planner schemas, read-only follow-up risk declarations,
    known agents and the spoke runner. Both independently enforce tenant subscription switches.
  - `StaffPreparationService.prepare(actor, ctx, input)`: local email draft, web-fetch request
    or invoice write draft. These staff services are not customer agents and have no HTTP route.
    `StaffActor` must come from the trusted backend identity layer, not from model/browser input.
  - Staff callers are restricted by tenant and role; mailbox references are tenant-bound;
    original invoices are tenant/contact-bound. Contact checks precede every preparation write.
- Migration `0260_staged_agent_requests.sql`: immutable preparation store, RLS without FORCE,
  contact-owner trigger, actor/trace recording, duplicate-safe keys and disabled customer
  capability provisioning. No billing, appointment or invoice is changed.
- Gateway readiness and migration test expectations were advanced to `0260`; a fully migrated
  disposable database is ready and a missing migration still produces 503.
- Shared parameterized SQL lives in `db/sql/agents/`. No dependencies or secret settings added.

## Request semantics

- Status is always `prepared`. Result text explicitly says **NOT executed**.
- No approval/notification is generated, no provider called, and no background processor exists.
  Records are a durable preparation ledger, not an executable queue.
- Same tenant/contact/agent/key + same input returns the original request ID. A changed
  action/payload rejects the key. Concurrent duplicate submissions persist once.
- Existing tenants get disabled booking/subscription capabilities; new-tenant onboarding must
  call `seed_preparation_capabilities(client_id)` and sync the input schemas before enabling.
- The application role has no update/delete permission on preparation records. Before using
  live mailbox text, define the retention/redaction policy and owner-authorized purge procedure.

## Remaining Wave 2/3 work

| Area | Still needed |
|---|---|
| Calendar | Tenant-bound encrypted OAuth; live free/busy, conflict recheck, event idempotency and timeout recovery, create/reschedule/cancel, test-calendar acceptance. |
| Email | OAuth, tenant/thread authorization, triage/summary/labels, drafts in Gmail, follow-up scheduling, approved send/outbox and restricted-scope verification. Draft text is currently staff-provided, not generated or sent. |
| Research | Tenant domain allowlist; isolated no-secret worker; DNS resolution-to-connect pinning, redirects, robots/terms, size/rate/cache limits, extracted-source provenance, scheduled runs and reviewed KB writes. No network fetches until these are built. |
| Subscriptions | Select provider; hosted checkout, verified webhooks, transaction/event idempotency, confirmed and approved financial writes, renewal/dunning jobs. |
| Invoices/MyInvois | Reviewed legal/tax data model, invoice issuance/PDF storage/serving, accounting adapter, taxpayer sandbox and MyInvois validation/signing/submission. Draft arithmetic is not legal validation. Existing signed-download-route/signing-fallback gaps in LG15 remain. |
| Console | Verified actor/RBAC adapter, staff preparation list/detail/review, approvals and lifecycle commands. Actual UI migration needs console source. |

Preparing every field in an existing request does not authorize its execution. Any future
executor must revalidate ownership/current state, require the appropriate confirmation/HITL,
and use the existing LG07 single-use, expiry, audit and idempotent delivery patterns.

## Keys and values

**Nothing needs adding to `.env.local` for this implemented slice.** Do not open it or copy
secrets into a chat. For future live integration:

- Google: dedicated test account/calendar/mailbox; OAuth web client and exact dev redirect
  URL; enable Calendar/Gmail APIs and consent test users. The existing `.env.example`
  `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` entries are future
  adapter placeholders, not configuration consumed by these preparation services. Refresh
  tokens and selected mailbox/calendar must be encrypted and tenant-bound, not global secrets.
- MyInvois: sandbox taxpayer access, TIN and registered application credentials, plus signing
  material if required by the implemented document version. Define variable/connection
  contracts only when the adapter is implemented; no new MyInvois environment names exist yet.
- Payments: no provider chosen; do not add Stripe/Billplz/Toyyibpay keys yet.
- Research: no API key required for preparation. Worker egress/domain configuration remains
  a prerequisite, not an invitation to fetch arbitrary URLs from this host.

## Verification

Synthetic-only tests are in `tests/test_staged_agents.py`. They cover validators, DST
(including Python ZoneInfo fold/nonexistent-time inputs), Decimal rounding and the exact
persisted amount ceiling, role/tenant/contact checks, disabled provisioning, concurrent deduplication,
changed-key input, unchanged booking/billing state, RLS, immutable records and a real
`make db-reset` against a new database inside a disposable test container. No existing dev or
production DB was reset or migrated.

Final preparation/identifier selection: **52 passed** (51 preparation tests); `make lint`
passed. An earlier planner/coordinator/worker selection passed 117 tests. Final full run:
**499 passed, 1 failed** in 16m19s; the TCP ingress benchmark measured p95 440.25 ms versus
the unchanged 200 ms limit. The exact TCP benchmark passed in isolation (1 passed, 135s).
Do not claim a clean full-suite pass. No Google, payment, MyInvois, live console or
research-fetch integration has been exercised.

## Next-session prompt

```text
Continue from docs/handoffs/2026-10-15-wave23-preparation.md. Read AGENTS.md, LG13,
LG15 and LG07 before editing. Preserve the provider-neutral preparation contracts and
never treat a prepared record as authorization to execute.

Console source is at CSA_WebConsole/; use the source-based migration plan for console
work. Build the next provider integration only after
confirming its test-account setup and scope with the user. Prefer Google Calendar first:
tenant-bound OAuth, availability, deterministic event IDs, conflict recheck, and approved
book/reschedule/cancel actions with synthetic HTTP transports and a dedicated test calendar.
Do not wire mailbox/research/invoice-write preparation into the customer planner.

Never read or print .env.local, secrets or production data. Ask for variable names and
test-account setup only. Do not touch existing production/development processes, webhooks,
Caddy/Cloudflare or n8n. Use disposable databases for migration/reset verification.
Run focused tests and make lint test; update LG13, PROGRESS and the handoff with exact
evidence and any remaining live gates.
```
