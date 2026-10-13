# LG13 — Agent catalog roadmap and web-console contract

## CONTEXT FOR CODING AGENT
LG05 delivers the **spoke framework** plus the order, escalation, ticket, notifier and staff-approval
spokes. Every other agent in this file is built **on that framework** using the LG05 "Add a new agent in a
day" checklist. None of them is part of LG05. They start after LG07 (compose, verify, gate, HITL, outbox),
because each customer-visible or irreversible action needs the approval and delivery path.

Rules for every agent below:
- Facts only: agents return `Fact`/`Ui`/`AgentError`, never prose for the customer.
- Declared `side_effects` (`none`/`reversible`/`irreversible`) and a risk class in `capability_registry`.
- Tenant-enabled via `capability_registry.client_enabled`; per-tenant settings validated by `config_schema`.
- Ids, tenants, accounts come from the envelope/context, never from model output.
- Live external accounts are gated: build against fakes and a test account, then enable per tenant.
- Credentials are encrypted per tenant (`csa.core.crypto`), never in source, logs or prompts.

> **Status (agent contract built):** every action declares a risk class (`read` / `draft` / `commit`,
> `csa/contracts/plan.py` `RiskClass`, `Agent.risk()`), and the coordinator runs a bounded read-only agent
> loop (`observe` node; `LLMProvider.next_step`; tenant limit `policy.agent_loop_max_steps`). New agents must
> declare `ACTION_RISK` so the loop and the console know which actions are safe to run unattended.
> Evidence: `tests/test_coordinator.py` (loop tests), `tests/test_llm_provider.py` (next_step, risk classes).

## Where each request lands
| Request | Phase | Why |
|---|---|---|
| Booking agent (Google Calendar) | Already in LG05 outline; build as wave 2 (needs OAuth + test calendar) | Spec and acceptance gate exist in LG05 |
| Email management agent | Wave 2 | Builds on the Gmail channel from LG03 |
| KB agent | Wave 1 | Needed first; also feeds the webscraping and teach agents |
| Invoice agent | Wave 1 (lookup) / Wave 3 (create, send, e-invoice) | Views exist; creation is financial |
| Subscription agent | Wave 3 | Needs a payment provider and webhooks |
| Teach-a-task agent | Wave 3 | Needs stable spokes to compose from |
| Webscraping agent | Wave 2, as a scheduled job | Not a live customer-turn tool |
| Future agents | Intake template at the end | |
| Web console | Parallel track: API contract (below) | Another developer builds the UI |

## Wave 1 — after LG07
### kb_agent (knowledge base)
- Retrieval over TEI embeddings + pgvector; returns `kind="verbatim"` chunks with source; logs misses to `kb_gaps`.
- Ingestion: console uploads (PDF, docx, URL, FAQ rows) → chunk → embed, per tenant, versioned, with delete and re-index.
- Suggestion: every fact carries `source_id` and `as_of`; staff can see "unanswered questions" from `kb_gaps` and answer them, which becomes new KB content after approval.
- Safety: ingested text is untrusted data; never treated as instructions.

### invoice_agent (read side)
- Lookup and status from `v_invoice_status`; "send me my invoice" returns a signed, expiring link, not a raw attachment path.
- Authorisation: only the contact the invoice belongs to.

## Wave 2
### booking_agent
Per the LG05 Google Calendar section: list/check/book/reschedule/cancel, idempotent event creation, time zone and DST tests, tenant-bound OAuth tokens. Suggestion: start with booking against a calendar the tenant owns; shared calendars come later. Needs a Google Cloud project, OAuth consent configuration and a test calendar.

### email_agent (mailbox management)
Different from the Gmail **channel** (LG03), where a customer's email becomes a conversation. This agent manages a staff or tenant mailbox.
- Capabilities: triage and label, summarise threads, extract action items, draft replies, schedule follow-ups.
- Suggestion: draft-only at first. Sending always goes through the HITL approval queue; auto-send is a per-tenant opt-in per category, later.
- Needs Gmail API with the narrowest scopes (`gmail.readonly` + `gmail.compose`; avoid `gmail.modify`/full mail unless required), Pub/Sub push, and for public use Google's restricted-scope verification. Microsoft 365/Outlook via Graph is a later adapter behind the same interface.
- Safety: email bodies are untrusted; prompt-injection in an email must not trigger tools.

### web_research_agent (webscraping) — practical enterprise use
Do **not** run it live inside a customer turn. Run it as scheduled/on-demand jobs (LG10 pattern) that write results to tables or the KB, which spokes then read.
Practical uses:
1. Competitor price and stock monitoring, with alerts to staff on thresholds.
2. Supplier/distributor catalogue and stock sync where no API exists.
3. Product enrichment (specs, images) into the catalogue after staff approval.
4. Lead/company enrichment for sales from public pages.
5. Regulatory, tariff, news or policy-change monitoring with summaries.
6. Ingesting a tenant's own website into the KB.

Guardrails (required, not optional):
- Domain allowlist per tenant; prefer official APIs/feeds before scraping.
- Respect robots.txt and site terms; no logging in to third parties, no CAPTCHA bypass.
- SSRF protection: block private/link-local IPs, resolve DNS then connect, limit redirects and size.
- Per-domain rate limits, timeouts, caching, and a user-agent that identifies the service.
- Extracted data stored with source URL and fetched-at; treated as untrusted; changes need staff review before reaching customers.
- Fetching runs in an isolated worker with no access to secrets or the production network.
- Suggested stack: `httpx` for static pages, Playwright only when unavoidable. Pin versions per AGENTS.md.

## Wave 3
### subscription_agent
- Customer asks about plan, next billing date, pause, cancel, change plan; staff define plans.
- Money moves only through a payment provider's hosted checkout and webhooks (no card data stored). Candidates for Malaysia: Stripe, Billplz, Toyyibpay; choose with the tenant.
- Irreversible actions (cancel, charge, refund) need confirmation buttons, idempotency keys, webhook signature verification, and HITL for refunds. Dunning (failed payment) runs as a scheduled job.
- Needs: provider account in test mode, webhook endpoint, plan/subscription tables (a new migration in LG11 style).

### invoice_agent (write side) and e-invoice
- Create, send, void, credit note; accounting sync (Xero, QuickBooks, AutoCount, SQL Account) through adapters.
- LHDN MyInvois e-invoice per the LG05 outline: legal/financial, so deterministic code with tests, never model-generated numbers. Needs a registered taxpayer sandbox.

### teach_task_agent ("teach a task")
Interpretation: a tenant admin teaches the system a repeatable procedure (SOP) in plain language, for example "when a customer asks for a size exchange, check the order, open a ticket, and tell them the policy".
- The model **compiles** the description into a declarative, versioned playbook: ordered steps calling existing whitelisted agents with typed inputs, conditions, and an optional approval step. It never generates or runs code.
- Admin reviews a rendered preview, runs it in a sandbox against synthetic data, then publishes. Playbooks are tenant-scoped, versioned, rollback-able, and audited.
- At runtime the planner can select a playbook as a plan template; each step still goes through the normal wrapper, gate and HITL, so a taught task can never exceed the permissions of its steps.
- Needs a `playbooks` table and a plan-template hook in LG06, so build after LG06/LG07.

## Intake template for future agents (add over time)
For each proposed agent, write down: purpose, who triggers it (customer, staff, schedule), side effects and risk class, external accounts and API scopes, data it reads/writes, HITL requirement, per-tenant config, failure behaviour (what facts/hints when down), and acceptance tests. Then follow the LG05 day-one checklist. Candidates noted so far: refund/warranty status, e-commerce marketplace sync (Shopee/TikTok), payments reconciliation, reporting/digest agent.

## Web console (separate developer)
Boundaries:
- The console talks only to a console API I own (`apps/console_api`, FastAPI, OpenAPI published). It never connects to Postgres directly and never sees provider secrets (secrets are write-only fields).
- Authorisation is server-side: every request resolves to `client_id` + role and runs under the same RLS tenant GUC as the runtime.
- The console can only read and write through the contracts below; agent behaviour changes via config, not code.

Planned API surface (v1):
1. Auth/session, roles (platform admin, tenant admin, tenant staff, read-only).
2. Conversations: list/search, transcript, take over, release, reply as human (ties to `owned_by`/`ai_muted_until`).
3. Approval queue: order, outbound-message and email-draft approvals (approve, edit, reject).
4. Orders, products/stock, tickets, escalations, invoices/subscriptions (read first, writes later).
5. Agent settings: enable/disable per tenant, config forms generated from each agent's `config_schema`, circuit state view.
6. KB: upload, list, delete, re-index, `kb_gaps` inbox.
7. Channels and integrations: connect Telegram/WhatsApp/Gmail/Calendar (OAuth start/callback, status, revoke).
8. Notification targets, quiet hours, staff users.
9. Playbooks (teach-a-task): draft, preview, test, publish, rollback.
10. Audit log, run history (`agent_runs`), dashboards (volumes, latency, escalation rate, DLQ).
11. Live updates via SSE.

What I need from the console developer / you:
- Identity: who authenticates users (own login, Google/Microsoft SSO, or a provider like Auth0/Supabase/Keycloak)? How are staff mapped to tenants?
- Roles and per-role permissions, and whether one user can belong to multiple tenants.
- Stack and hosting (framework, same host or cloud, domain, CORS/cookie vs bearer token).
- Which screens come first; whether the client-facing console and the admin console are one app with roles or two apps.
- Whether they need realtime (SSE/websocket), file upload size limits, and i18n/time zones.
- Whether they want to generate a typed client from my OpenAPI file (recommended).
- Deliverables from me: OpenAPI spec, a seeded dev tenant (sandbox-amsyar / sandbox-ihsan already exist), a mock server with fixtures so they can start before the API is real, and an error-code catalogue.
- Rules for them: no production data in dev, no secrets in the browser, no direct DB access.

Suggested order: publish the OpenAPI contract and mock server first (days), then build read-only screens (conversations, orders, runs), then the approval queue (needs LG07), then settings/KB/integrations.

## Acceptance criteria
- [ ] Each agent has an intake sheet, accepted by you before coding starts.
- [ ] Each agent passes the LG05 wrapper tests, a tenant-isolation test, a wrong-contact test where relevant, and an idempotency test for every write.
- [ ] Live-account use is gated by a test-account integration run.
- [ ] Console API OpenAPI spec reviewed with the console developer before they build against it.
- [ ] No console code path reads the database or a provider secret directly.
