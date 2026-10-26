# LG13 — Agent catalog roadmap and web-console contract

## Console M2 implementation update (2026-10-15)

The adopted Next.js console now uses the session-scoped agent catalogue and recorded
runtime APIs described in [CONSOLE_API.md](../CONSOLE_API.md). Subscription, tenant switch,
implementation and setup/circuit states are independent. Specialised inspection navigation
does not imply that the feature-management/OAuth screens in the roadmap below exist.
Booking/subscription writes remain unexecuted preparation; default e-commerce runtime has
no adapters; MyInvois and invoice PDF delivery are unavailable. Configured TEI is unverified,
with matching/incompatible active chunk counts instead of fabricated semantic-health metrics.

Production spoke attempts freshly check both tenant registry switches, including callbacks
and retries; console KB execution and upload-job claims are also gated. Disabled queued KB
jobs do not consume attempts; already-started work is not cancelled. Scoped run metadata uses
ID-keyset pagination and existing trace-event polling, without raw agent payloads.
Focused and full-suite/isolated-load evidence, including limitations, is maintained in
[PROGRESS.md](../PROGRESS.md). No schema change, persistent database reset, provider execution
or authenticated M2 live-browser acceptance is claimed by this implementation.

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
| E-Commerce operations agent (TikTok Shop, Shopee, Lazada) | M6 staged/offline | Follow the current [M6 roadmap](../archive/handoffs/2026-10-15-console-source-migration-plan.md#m6--business-tabs-integrations-and-staff-preparation); do not use the superseded 12-Oct draft as an implementation contract. |
| Booking agent (Google Calendar) | Already in LG05 outline; build as wave 2 (needs OAuth + test calendar) | Spec and acceptance gate exist in LG05 |
| Email management agent | Wave 2 | Builds on the Gmail channel from LG03 |
| KB agent | Wave 1 | Needed first; also feeds the webscraping agent |
| Invoice agent | Wave 1 (lookup) / Wave 3 (create, send, e-invoice / LHDN) | Views exist; creation is financial |
| Subscription agent | Wave 3 | Needs a payment provider and webhooks |
| Webscraping agent | Wave 2, as a scheduled job | Not a live customer-turn tool |
| Teach-a-task (Grokbot Virtual Desktop) | Dedicated Phase: LG14 | Interactive desktop session, user action recording, and scheduled replay |
| Future agents | Intake template at the end | |
| Web console | Parallel track: API contract (below) | Admin subscription toggles & specialized agent tabs |

## Wave 1 — after LG07
### kb_agent (knowledge base)
- Retrieval over TEI embeddings + pgvector; returns `kind="verbatim"` chunks with source; logs misses to `kb_gaps`.
- Ingestion: console uploads (PDF, docx, URL, FAQ rows) → chunk → embed, per tenant, versioned, with delete and re-index.

#### KB console upload contract (M3, 2026-10-15)
- The authenticated console posts repeated multipart `files` to `/console/v1/kb/uploads`
  with a UUID `Idempotency-Key`. Only human platform/tenant admins and staff may upload;
  tenant context and CSRF use the established identity path. Server-generated UUID
  filenames are published durably under `KB_UPLOAD_ROOT/<client_id>/` before the
  batch/file/queue/audit transaction commits. Up to 3 PDF/DOCX/CSV files, 10 MiB each,
  31 MiB aggregate and bounded multipart headers. Invalid/truncated/type/signature/
  empty/path/oversize requests fail explicitly. Same key/fingerprint returns the original
  202 job; changed content is 409. No base64 or client-selected server paths.
- Internal `kb_ingest_jobs.files` remains compatible with legacy path strings or objects
  `{path,name,title?,category?,language?}`; managed console entries also carry `file_id`.
  These trusted internal paths are **not** the browser upload contract. Public DTOs omit
  paths/requesters/arbitrary metadata; original download uses an authorized source UUID.
- The CSA worker (`csa/agents/kb/ingest_jobs.py`, enabled with `KB_INGEST_ENABLED=true`) claims jobs with `FOR UPDATE SKIP LOCKED`. It then rejects paths outside the client directory (including through symlinks), checks file type by signature, and extracts text. PDF text is extracted per page; scanned PDFs without a text layer need OCR first. DOCX uses headings and tables. For CSV, `question`/`answer` (or `soalan`/`jawapan`) columns become FAQ rows; otherwise each row is stored as text. The worker chunks the text (≤600 chars), embeds it, and stores it.
- Limits: 3 files per job, 10 MB per file, 500 PDF pages, 5000 CSV rows. Encrypted PDFs, legacy `.doc` files, and empty or unreadable files fail with a per-file error.
- The job ends with status `done`, `partial` (some files failed), or `failed`. Internal
  `meta.results` includes status (`ingested`/`duplicate`/`failed`), document/chunk/version
  metadata, replacements and safe file errors. Curated public results omit paths and
  replacement internals. `/knowledge-base` and `/portal/faq` poll jobs, originals,
  document versions/model compatibility and gaps every five seconds; queued is not searchable.
- Identical content is reported as `duplicate` and is not embedded again. Re-uploading the same file name with new content creates version N+1 and deactivates the old version and its chunks.
- Migration 0280 adds tenant-scoped upload batches/files and hash deletion tombstones.
  Shared tenant advisory locks serialize final publication/deletion, not embedding calls.
  Checks before deduplication and after embedding fence deleted managed files and content
  deleted after a legacy/managed job was queued. Retries cannot resurrect deleted content;
  deliberate uploads queued after deletion may ingest again.
- Source/document deletion is confirmed in the UI, scoped and audited, removing chunks
  and all original aliases linked to a shared duplicate document. Readers can download
  but not mutate. Already-open downloads may finish during deletion. Storage reconciliation
  is an explicit audited action under the same lock, removing UUID-pattern orphan/partial/
  deleted files while retaining active metadata-backed sources and unknown legacy names.
  See [API and recovery contract](../CONSOLE_API.md#knowledge-base-lg13--m3).
- Rollout requires a forward-only 0280 migration, shared writable upload root and
  deliberate gateway/ingestion-worker startup. The user-authorized isolated-dev rollout
  completed without reset: platform-admin three-format ingestion through loopback TEI,
  downloads, duplicates, deletion and reconciliation pass. A guarded KB-only task claims
  only the synthetic M1 tenant; no full runtime or old FAQ import was started. See
  [startup and scope](../CONSOLE_API.md#isolated-dev-rollout-prerequisites) and
  [live evidence/remaining checks](../archive/PROGRESS-2026-10-early.md#m3-authenticated-kb-upload-and-lifecycle-2026-10-15).
- If TEI is unavailable, the job goes back to the queue and is retried with a delay, up to 3 attempts, then fails. The worker never silently falls back to a different embedding. Search only compares chunks with the same `embed_model`, so hash-embedded (dev) and TEI-embedded content are never mixed.
- Embeddings: `TEI_ENDPOINT` (required in prod). The dev TEI runs `intfloat/multilingual-e5-small` (384-dim) and uses the `TEI_QUERY_PREFIX="query: "` and `TEI_PASSAGE_PREFIX="passage: "` prefixes. Without TEI, dev and ci use a lexical `hash-384` embedder.
- Cutover: confirm the legacy n8n `kb_documents.embed_model` label and model. If they don't match `KB_EMBED_MODEL_LABEL`, re-index.
- Suggestion: every fact carries `source_id` and `as_of`; staff can see "unanswered questions" from `kb_gaps` and answer them, which becomes new KB content after approval.
- Safety: ingested text is untrusted data; never treated as instructions.

### invoice_agent (read side)
- Lookup and status from `v_invoice_status`; "send me my invoice" returns a signed, expiring link, not a raw attachment path.
- Authorisation: only the contact the invoice belongs to.

## Wave 2

### Provider-neutral preparation slice (2026-10-15)
The user selected backend preparation for all waves before choosing payment providers or
supplying the console source. This is **not completion of the external integrations below**.
Implementation: `csa/agents/staged/models.py`, `csa/agents/staged/service.py`;
migration `0260_staged_agent_requests.sql`. See
[the latest handoff](../archive/handoffs/2026-10-15-wave23-preparation.md).

| Surface | Implemented now | Still gated / not built |
|---|---|---|
| `booking_agent` | Contact-scoped local `list`/`status`; `request_book`, `request_reschedule`, `request_cancel` persist a preparation record. Offset-aware times, IANA zone/DST consistency, future start and ≤24-hour duration checks. | Google OAuth, free/busy, conflict checks, Calendar event create/update/delete and timeout recovery. Local history is not live availability. |
| Email preparation | Staff-only `EmailDraftInput` through `StaffPreparationService`; tenant-bound Gmail credential reference, bounded reply body and header validation. Saves a local draft, never a Gmail draft or sent message. | Mailbox/thread read authorization, triage/labels/summaries, Gmail draft creation, follow-ups, approval/send and Google OAuth. |
| Web research preparation | Staff-only `ResearchInput`; ≤10 HTTPS URLs, rejects obvious unsafe address syntax and IP literals, saves an unexecuted request. | Isolated fetcher, tenant allowlists, DNS pinning/rebinding protection, robots/terms checks, redirect/size/rate/cache controls, schedule and KB ingestion. **No URLs are fetched.** |
| `subscription_agent` | Active plan catalogue, contact-scoped local `list`/`status`; pause/cancel/change requests validate ownership and target plan and persist without changing subscriptions. | Payment provider selection, checkout, signed webhooks, confirmation/HITL, billing changes and dunning. |
| Invoice write preparation | Staff-only `InvoiceDraftInput`; create/credit drafts and void requests, original-invoice ownership checks; explicit Decimal quantities/prices/tax rates, per-line half-up rounding and amount bounds. | Invoice issuance/PDF delivery, void/credit execution, accounting adapters and MyInvois sandbox validation/signing/submission. These are calculations, not tax/legal validation. |

Only booking/subscription are registered with the customer planner/runner. Their new registry
entries are disabled by default; both switches must be on even for reads. Staff email, research
and invoice writes are library services only, intentionally absent from customer planning and
HTTP routes until authenticated staff adapters exist.

All preparations are immutable, tenant/contact-scoped `agent_preparation_requests` records
(`status='prepared'`, actor, trace, payload, unique idempotency key). Replays return the same ID;
changed input with the same key is rejected. They **do not** create approval rows, send staff
notifications, reserve availability, mutate billing/invoices or execute any external action.
Future executable actions must use LG07 approval/confirmation and outbox semantics; replying
with text approval is not sufficient. There is no preparation-request processing worker.

Existing tenants receive disabled booking/subscription rows during migration. For a new tenant,
onboarding must call `seed_preparation_capabilities(client_id)` and sync each input schema via
`CapabilityRegistry.sync_input_schema` before deliberately enabling it. The function never
overwrites existing capability choices.

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

### teach_task_agent ("teach a task" / Grokbot virtual desktop)
Moved to dedicated phase **LG14** (`docs/langgraph-rebuild/LG14_teach_task_desktop_agent.md`).
Provides an interactive sandboxed desktop environment where users record desktop actions, compile them into declarative playbooks with typed inputs, and schedule them to run on periodic or conditional triggers.

## Intake template for future agents (add over time)
For each proposed agent, write down: purpose, who triggers it (customer, staff, schedule), side effects and risk class, external accounts and API scopes, data it reads/writes, HITL requirement, per-tenant config, failure behaviour (what facts/hints when down), and acceptance tests. Then follow the LG05 day-one checklist. Candidates noted so far: refund/warranty status, e-commerce marketplace sync (Shopee/TikTok), payments reconciliation, reporting/digest agent.

## Web console

Source supplied 2026-10-15 at `CSA_WebConsole/csa-console/`. The user approved retaining
the Next.js UI and replacing its API with one CSAV2 backend, session authentication and
membership-based roles. PM/terminal/Hermes/workroom features are deferred.
See [the detailed migration plan](../archive/handoffs/2026-10-15-console-source-migration-plan.md).
The feature catalogue below remains a roadmap, not a claim of implemented endpoints.

Boundaries:
- The console talks only to the console API (`csa/console/`, mounted by the gateway at `/console/v1` when `CONSOLE_API_TOKEN` is set; OpenAPI at `/openapi.json`). Full n8n-console turnover plan: `LG15_console_turnover.md`. It never connects to Postgres directly and never sees provider secrets (secrets are write-only fields).
- Authorisation is server-side: verified sessions/memberships resolve to `client_id` + role,
  and domain access runs under the same RLS tenant GUC as the runtime. The current
  shared-token API must be adapted; browser-supplied actor headers are not trusted.
- The console can only read and write through the contracts below; agent behaviour changes via config, not code.

Planned API surface (v1):
1. **Auth & RBAC**: Roles (`platform_admin`, `tenant_admin`, `tenant_staff`, `read_only`).
2. **Platform Admin — Subscription Agent Management**:
   - List and toggle active agents per client based on subscribed tiers (`capability_registry.is_enabled`).
   - Audit trail of enabled/disabled capabilities.
3. **Tenant Admin — Agent Configuration**:
   - Toggle subscribed agents (`capability_registry.client_enabled`).
   - Dynamic configuration forms generated from `capability_registry.config_schema`.
   - Circuit breaker status and manual reset buttons (`csa.ops.runbooks`).
4. **Specialized Agent Tabs**:
   - **LHDN MyInvois Tab** (`/console/v1/agents/lhdn`):
     - E-invoice generation & submission history.
     - Validation status against LHDN MyInvois API (TIN, BRN, MSIC codes).
     - Credit note, debit note, and refund invoice issuance.
     - Rejection error diagnostics and manual re-submission.
   - **E-Commerce Operations Tab** (`/console/v1/agents/ecommerce`):
     - Store connection management (TikTok Shop OAuth, Shopee, Lazada).
     - Cross-marketplace order stream with channel badges.
     - Unified stock matrix with one-click "Sync All" to marketplaces.
     - Dispute & cancellation inbox.
   - **Bookings Tab** (`/console/v1/agents/bookings`):
     - Google Calendar OAuth status & calendar binding.
     - Service slots, working hours, and blackout windows.
   - **Knowledge Base Tab** (`/console/v1/agents/kb`):
     - Document upload (PDF, docx, URLs), chunking & vectorization status.
     - `kb_gaps` inbox displaying unanswered questions for staff resolution.
5. **Conversations**: List/search, transcript, take over, release, reply as human (ties to `owned_by`/`ai_muted_until`).
6. **Approval Queue**: Order, outbound-message and email-draft approvals (approve, edit, reject).
7. **Channels & Integrations**: Connect Telegram / WhatsApp / Gmail / Calendar / TikTok Shop (OAuth start/callback, status, revoke).
8. **Notification Targets**: Quiet hours, staff users, priority thresholds.
9. **Audit Log & Observability**: Run history (`agent_runs`), timelines, DLQ inspection and redrive.
10. **Live Updates**: Server-Sent Events (SSE) for timeline events and approval alerts.

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

## Acceptance criteria & Verification Evidence

> The checked items and measurements below are dated LG13 implementation evidence,
> not current release acceptance. Current source registers `EcommerceAgent`, but its
> default instance has no platform adapters. The M6B verifier is not wired as a
> callback/runtime flow, and no live seller authorization/provider acceptance is
> established. Consult [current progress](../PROGRESS.md), the [M6 roadmap](../archive/handoffs/2026-10-15-console-source-migration-plan.md#m6--business-tabs-integrations-and-staff-preparation)
> and the source/tests before relying on these historical counts or claims.

- [x] Agent intake/design material exists. The old ecommerce proposal was superseded by the M6 roadmap; this checklist entry does not establish live marketplace readiness.
- [x] At the time of this roadmap entry, Wave 1 and drafted agents had implementations with declared `ACTION_RISK`, tenant isolation, and contact security; each capability still requires current source/acceptance review:
  - `ecommerce_agent` (`csa/agents/ecommerce/`): Spoke contract and adapter groundwork. The default instance is constructed without adapters; this does not imply usable TikTok/Shopee/Lazada operations.
  - `kb_agent` (`csa/agents/kb/`): 384-dimensional vector embeddings, cosine distance chunk retrieval, auto-logging to `kb_gaps`, chunking and ingestion, gap resolution.
  - `invoice_agent` (`csa/agents/invoices/`): Contact-isolated status lookup, listing, and HMAC-SHA256 signed expiring download URLs (15-minute TTL).
  - Capability/agent registrations exist in `csa/turn/deps.py`; they do not by themselves prove provider dependencies are configured or live.
- [x] Console API (`csa/console/router.py`, `csa/console/capabilities.py`):
  - Admin plan subscription (`is_enabled`) vs client self-service toggle (`client_enabled`) on `/console/v1/capabilities`.
  - Specialized tabs catalogue (`/console/v1/tabs/specialized`) covering LHDN MyInvois,
    E-Commerce, KB, Invoices, Bookings and Subscriptions, with explicit implementation/
    setup/permission metadata. No hard-coded healthy/compliance metrics.
  - KB listing, plain-text ingestion, ingest-job status, deletion and gap endpoints.
    Authenticated multipart upload and document lifecycle UI remain M3, not implemented here.
  - Invoice overview; the console download-link endpoint returns 501 until M6 provides
    real artifacts. Existing signature-library tests do not establish working PDF delivery.
- [x] Tests:
  - `tests/test_ecommerce_agent.py`: 4 passed (signing, webhook verification, inventory, HITL gate).
  - `tests/test_kb_agent.py`: 7 passed (embedding dimensions/normalization, query match, gap logging, ingest/delete).
  - `tests/test_kb_ingest.py`: 25 passed (PDF/DOCX/CSV extraction, type/signature allowlist, path traversal/absolute/symlink confinement, size/empty limits, strict TEI errors, batching and e5 prefixes, 3-file job searchable, tenant isolation, >3 files rejected, partial results, duplicate/versioning, TEI outage backoff→failed, concurrent single claim, stale-job requeue).
  - Live dev check: local TEI (`multilingual-e5-small`) returned 384-dim vectors; an English query ranked the Malay opening-hours passage highest (0.85 vs 0.78/0.77).
  - `tests/test_invoice_agent.py`: 5 passed (contact isolation, signature verification, status, signed URL).
  - `tests/test_console_capabilities.py`: 3 passed (capability listing/toggling, specialized tabs, invoice downloads).
  - `tests/test_staged_agents.py`: 51 passed (Wave 2/3 preparation, not provider execution).
  - `make lint` passed. Latest full-suite evidence is in `docs/PROGRESS.md`; do not equate
    focused passing selections with a clean full suite.
- [ ] Live-account use is gated by a test-account integration run (live credentials provided per tenant).
- [x] Wave 2/3 provider-neutral preparation implemented; no external-execution claim. Synthetic
  verification and live blockers are recorded in the 2026-10-15 handoff.
- [ ] Console API OpenAPI spec reviewed with the console developer before frontend cutover.
- [x] No console code path reads the database or a provider secret directly (tenant isolation via RLS GUC).
