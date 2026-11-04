# Recon: Assistant activity and Knowledge base (as they are now)

Worktree: `/home/amsyrzmi/CSAV2/.claude/worktrees/feat-console-redesign-phase-2` (branch feat/console-redesign-phase-2, clean at start).
Short names below: `WEB` = `CSA_WebConsole/csa-console/web`. Nothing was edited, built or run.
Note: `.claude/prompts/_common-context.md` does not exist in this worktree (only `AGENTS.md`, read). AGENTS.md rules that matter here: Pydantic contracts, SQL only in `db/sql/`, additive migrations, no silent fallbacks.

---------------------------------------------------------------------
## 0. Cross-cutting facts (both screens)

- Entry: nav key `activity` -> `/activity` (admin) or `/portal/activity`; `knowledge_base` -> `/knowledge-base` or `/portal/faq` (`WEB/lib/navigation.ts:38-39`). Four page files are 3-13 line wrappers that call `requireIdentity()` and render the component: `WEB/app/(admin)/activity/page.tsx`, `WEB/app/portal/activity/page.tsx`, `WEB/app/(admin)/knowledge-base/page.tsx`, `WEB/app/portal/faq/page.tsx`. Only the admin activity page reads `?client_id=`; both activity pages read `?agent=`. KB reads `?client_id=` itself via `useSearchParams` (`KnowledgeWorkspace.tsx:12,15`).
- There are NO `runtime-*.ts` files and Activity uses none of `services-view.ts` except in the dead "agents" mode (see 1.F). All Activity logic is inline in `RuntimeWorkspace.tsx`.
- Shared UI primitives: `WEB/components/console/ui.tsx` (`Heading`, `Panel`, `Notice`, `field`, `secondary`, `action`). `Notice` renders `<p role="status">`, or `role="alert"` when `error`/`warning` (ui.tsx:14-39). Neither screen uses `ListState`, `StatusLabel`, `Skeleton` or `FullPageLoading` from `components/console/fs/` (those exist and could be reused: `fs/ListStates.tsx` has kinds loading/empty/unavailable/partial/forbidden but its COPY is hard-wired to "conversations"; `fs/StatusLabel.tsx` has tones neutral/info/attention/success/error/customer/team).
- Server error mapping: `lib/api.ts` `decode()` turns `{detail:"package_inactive"}` into the sentence "This area is not part of your organisation's active service packages. Ask your administrator to activate it in Services." Any other detail string is shown raw. 5xx with no detail -> "Something went wrong on our side. Try again." Other non-JSON -> "Request failed (NNN)".
- All fetches go through `api()` to same-origin `/console/v1/*` (Next proxy; `proxy.ts` lists the four routes as session-required).
- Dates are rendered with `toLocaleString()` in the viewer's browser zone, NOT the tenant timezone (the `Client` type has `timezone`, e.g. Asia/Kuala_Lumpur; `Me` has no tenant timezone).
- Loading shell: `app/(admin)/loading.tsx` / `app/portal/loading.tsx` render `FullPageLoading` while the server page awaits `requireIdentity()`.

---------------------------------------------------------------------
# SCREEN 1: ASSISTANT ACTIVITY

Component: `WEB/components/console/RuntimeWorkspace.tsx` (382 lines). It is a two-mode component (`mode: "agents" | "activity"`, line 22); only `"activity"` is reachable (see 1.F).

## 1.A Layout today (on-screen order, exact wording)

Top level `RuntimeWorkspace` (lines 18-65):
1. `Heading` h1 "Assistant activity" (line 44) with description "What your assistants did recently: each run, whether it worked and how long it took. Message text is never shown." (line 47).
2. Error `Notice` (red, role=alert) with `error` text if the tenant list fails (line 48).
3. PLATFORM ADMIN ONLY (lines 49-58): label "Tenant" (`htmlFor="runtime-tenant"`) + `<select id="runtime-tenant">`; first option "Select a tenant" (or "Loading tenants..." while loading); then one option per tenant name. No `data-testid`.
4. If no tenant chosen (platform admin before picking, or a user with no active membership): `Notice` "Select a tenant with an active membership to continue." (line 59). Otherwise `RuntimeActivity` (keyed by clientId, so switching tenant remounts and resets everything).

`RuntimeActivity` (lines 267-340), in order:
5. Error `Notice` (red) with the raw error message string (line 305).
6. Panel (h2) "Last 24 hours (all agents)" (line 306), only when `overview` loaded:
   - "{runs} recorded runs; {succeeded} succeeded; {failed} failed." (line 307)
   - "Last run: {toLocaleString or "No runs in this window"}." (line 308)
7. Filter `<form>` (lines 310-318): text input labelled "Agent name (optional)" (maxLength 100; no placeholder; user must type an internal capability name such as `invoice_agent`), submit button "Filter / refresh". Submitting sets the filter, resets paging to newest, clears the loaded page and the selected run.
8. Info `Notice` (lines 319-320): "This list refreshes every 5 seconds. It shows when each run happened, not what was said. An empty list only means nothing has run yet; it does not mean everything is healthy."
9. Panel (h2) "Recorded runs" (line 321):
   - loading: `<p>` "Loading runs..." ; load failed: "Runtime data unavailable." (line 322)
   - empty: "Nothing has been recorded for this selection." (when a filter is set) or "Nothing has run yet. Runs appear here as your assistants work." (line 323)
   - rows: `<ul>` of full-width `<button>`s. Row text is TECHNICAL: `#{run.id} {run.agent} / {run.status} / {run.error_code ?? (run.ok ? "Succeeded" : "Failed")}` then a second line `{created_at toLocaleString} / {latency_ms ?? "Unknown"} ms` (lines 324-329). Example: "#12 invoice_agent / ok / Succeeded" + "2 Nov 2026, 10:41:07 / 17 ms". Failed rows show the raw error code (`circuit_open`, `timeout`, ...) instead of "Failed".
   - pager (lines 330-335): buttons "Newest" (disabled at newest page) and "Older runs" (disabled when `next_before_id` is null). There is no "Newer"/previous: only jump back to newest.
10. Selected-run panel `RunDetail` (lines 342-382), appears below the list after clicking a row:
    - Panel h2 `Run #{id}` (line 374)
    - Error notice "{error} (previously loaded detail may be stale)" (line 375)
    - `<p>` "{agent}: {status}. Trace: {trace_id}" (line 376; shows raw UUID)
    - h3 "Trace events" (line 377)
    - empty: "No recorded events yet." or "Loading detail..." (line 378)
    - `<ol>` lines: `#{event.id} {event.node} / {event.agent ?? "Coordinator"} / {event.status} / {event.duration_ms} ms` (line 380). Node names are graph-node internals.

Technical content to hide/translate in a redesign: run id `#N`; capability names like `invoice_agent`; `status` raw (`ok`/`error`); `error_code` raw; `latency_ms` shown as "N ms"; trace id UUID; "Trace events" with graph node names, event ids, `duration_ms`; the filter field requiring a capability name; the "Coordinator" label for null agent. No provider names, circuit or switch words, or raw JSON appear on THIS screen (those are on the Agent card, see 1.F). Message text/payloads are never returned by the API.

## 1.B Data

All calls are GET, tenant-scoped. For a platform admin every URL carries `client_id=<uuid>`; for a tenant member the query is empty and the server uses the session's tenant (`WEB/RuntimeWorkspace.tsx:40-41`; server `csa/console/access.py:tenant`). A tenant member passing a different `client_id` gets 403.

| # | Call | When | Params | Response |
|---|------|------|--------|----------|
| 1 | `GET /console/v1/admin/clients` | platform admin only, once | none | `Client[]` `{id,slug,name,timezone,status}` |
| 2 | `GET /console/v1/runs` | on mount, on filter/page change, then every 5 s | `limit=25` (hard-coded line 282), `before_id=<int>` (keyset cursor), `agent=<exact text>`, `client_id` (platform) | `RunPage {items: AgentRun[], next_before_id: number\|null}` |
| 3 | `GET /console/v1/overview` | same Promise.all as #2, every 5 s | `client_id` (platform) | `RuntimeOverview {runs:int, succeeded:int, failed:int, last_run_at: iso\|null, window_hours: 24}` |
| 4 | `GET /console/v1/runs/{id}` | RunDetail, every 5 s while open | `client_id` | `AgentRun` |
| 5 | `GET /console/v1/events` | RunDetail, every 5 s, with #4 in one Promise.all | `trace_id=<uuid>`, `after_id=<cursor, starts 0>`, `client_id`; no `limit` sent (server default 100, max 200) | `RuntimeEvent[] {id,node,agent\|null,status,duration_ms,created_at}` ordered by id ASC; appended client-side |

`AgentRun` (`WEB/lib/identity-contracts.ts:136-146`; server `csa/console/runtime.py:RunOut`): `id:int, trace_id:uuid, conversation_id:uuid|null, agent:string, ok:bool, status:string, error_code:string|null, error_category:string|null, latency_ms:int|null, created_at:datetime`. (The table also holds `task_id, model, prompt_tokens, completion_tokens, input, output` but they are NOT selected by the list SQL.)

Backend: `csa/console/router.py:181-212` (`/runs`, `/runs/{id}`), `csa/console/runtime.py`, SQL `db/sql/console/list_runs.sql`, `get_run.sql`, `runtime_overview.sql`, `list_events.sql`.

`list_runs.sql` in full:
`WHERE client_id = %s AND (before_id IS NULL OR id < before_id) AND (agent IS NULL OR agent = agent) ORDER BY id DESC LIMIT limit+1`.

- Pagination: keyset on `id DESC` (newest first). Page size: UI asks 25; server default 50; server max 200 (`timeline.MAX_PAGE`, router.py:187). `next_before_id` is set only when a row beyond the page exists (limit+1 trick, runtime.py:60-63). `before_id` must be 1..2^63-1 else 422.
- Polling: runs+overview every 5000 ms (RuntimeWorkspace.tsx:297, re-armed in `finally`, so it also keeps polling after an error); run detail+events every 5000 ms (line 368).
- Overview window is fixed at 24 h server-side (`now() - interval '24 hours'`), no parameters.

### Which server filters exist
| Filter | Exists? |
|---|---|
| Tenant | YES (`client_id` for platform admin; implicit for members; RLS also scopes) |
| Agent / capability | YES, `agent=` EXACT match on `agent_runs.agent` (1-100 chars). No list-of-agents or "in" form. No endpoint returns "distinct agents that have runs"; `/console/v1/agents` returns the catalogue, not run counts |
| Status / outcome (`ok`, `status`, `error_code`) | NO |
| Free-text search | NO |
| Trace / conversation | NO on `/runs`. (`/events` filters by `trace_id`) |
| Date range | NO. Nothing accepts `from`/`to`/`since`/`until`/`created_*`. Only `before_id`. |

### What a calendar date-range filter would need: NEEDS BACKEND CHANGE
- `/runs` has no date parameters at all; ordering is by `id DESC`, not `created_at`. The client cannot filter by date server-side. (`/traces` does take `before: datetime` but that is a different, trace-grouped endpoint over `agent_events`, one-sided, and returns no run outcome.)
- Backend change needed: add optional `created_from` / `created_to` (timestamptz, or date + tenant timezone) to `router.py` `/runs`, `runtime.list_runs` and `list_runs.sql` (add `AND (%s::timestamptz IS NULL OR created_at >= %s) AND (... created_at < %s)`).
- Index: existing indexes are `idx_agent_runs_client (client_id)`, `idx_agent_runs_agent (agent, created_at DESC)`, `idx_agent_runs_created (created_at DESC)`, `idx_agent_runs_trace` (`db/schema_baseline.sql:2326-2350`). There is no `(client_id, created_at)` index, so a per-tenant date range would scan by client index then filter; acceptable at small volumes, but a new migration for `(client_id, created_at DESC)` would be the clean fix (migrations are additive and numbered; the console-redesign schema window is per memory 0500+, the HR window 0520-0529; check `db/migrations` for the next free number and ownership).
- Keyset: `id` is a bigserial and `created_at` defaults to `now()`, so ids are roughly but not strictly monotonic with `created_at`. Keep `before_id` for paging inside a date window.
- Total count: NOT available. `RunPage` has only `items` and `next_before_id`. The only counts are the fixed 24 h `overview` (runs/succeeded/failed/last_run_at). A "N runs between X and Y" or page-count UI needs a new count field/endpoint (needs backend change); a "load more" UI works with what exists.
- Timezone: dates should be interpreted in the tenant's `Client.timezone`; `Me` does not expose it to tenant members (needs backend/contract change or fall back to browser zone as today).
- Retention: the schema defines `csa_retention()` deleting `agent_runs` older than 30 days (`db/schema_baseline.sql:~163-203`). I found no caller of it under `csa/`, `scripts/` or `deploy/` (not proven scheduled either way, treat as unverified). If it runs, a date picker reaching back more than 30 days finds nothing.
- Cheap fully-client-side alternative: since rows are ordered by id DESC, a client could page back until `created_at < from`, but that is O(pages) and unbounded; not recommended.

## 1.C "Last 5 agents used" feed

Source: `GET /console/v1/runs` (newest first) is the only per-run feed. Per row you get: `agent` (capability name), `ok`, `status`, `error_code`, `error_category`, `latency_ms`, `created_at`, `trace_id`, `conversation_id`. There is NO "started_at" and no "finished_at": `created_at` is the INSERT time, which is AFTER the run finished.

Writer: `csa/agents/recorder.py:PostgresRunRecorder.record` is called once per task from `SpokeRunner._finish` (`csa/agents/runner.py:248`) after retries, timeouts, breaker and capability checks. So:
- Every `agent_runs` row is TERMINAL. There is no queued / running / in-progress row. "Working right now" cannot be derived from runs.
- `latency_ms` is the wall time of the whole task including retries/backoff (set at finish); can be null in the type but the recorder always writes an int.

Distinct `status` values the backend can write: contract is `Literal["ok","partial","error"]` (`csa/contracts/facts.py:92`; DB column is free `text DEFAULT 'ok'`, no CHECK found). In the code, only `"ok"` and `"error"` are ever produced (`ok=True` <-> `status="ok"`, `ok=False` <-> `status="error"`; grep finds no producer of `"partial"` for agent results). Treat `partial` as possible-but-unused. All are terminal. `ok` is always consistent with status (`ok == (status=="ok")`).

`error_category` (only when error): `validation | not_found | conflict | dependency_unmet | upstream | internal`.
`error_code` set by the runner itself (`csa/agents/runner.py`): `sandbox_action_forbidden` (validation), `agent_not_registered` (not_found), `invalid_input` (validation), `capability_unavailable` (validation; agent switched off/not subscribed), `circuit_open` (upstream; failure guard tripped), `timeout` (upstream, retryable), `agent_exception` (internal). Agent-level codes seen in code (non-exhaustive): `embedding_unavailable`, `gap_not_found`, `mixed_currency`, `not_authorised`, `staff_not_authorised`, `order_not_found`, `product_not_found`, `ticket_not_found`, `invoice_not_found`, `booking_*`, `subscription_*`, `plan_not_found`, `idempotency_conflict`, `invalid_key`, `forbidden`, `contact_not_found`, `mailbox_not_found`, `disabled`, `order_not_cancellable`, `order_not_confirmable`, `order_not_pending`, `insufficient_stock`, `ticket_closed`, `reopen_window_passed`, `ecommerce_platform_error`, `unsupported_platform`, `channel_unsupported`, `action_requires_approval`, `unauthorized`, `conflict`, `validation`, `not_found`. (`spoke_execution_failed` is produced in `csa/graph/coordinator.py:832` but that result is not written to `agent_runs`.)

Distinct `agent` values that appear in `agent_runs` (the 9 registered in `csa/turn/deps.py:144-157`): `escalation_agent, order_agent, ticket_agent, order_approval, ecommerce_agent, kb_agent, invoice_agent, booking_agent, subscription_agent`. NOT recorded here: the HR assistant, Email assistant, Workspace assistant, staff preparation service (`email_agent`, `web_research_agent`, `invoice_write_agent` run outside SpokeRunner), `lhdn_agent` (not implemented). The DB view in the baseline also references a legacy `store_agent`; not produced by current code. So HR / email / assistant / support avatars can never come from this feed today (needs backend change if those should appear: they would need to write `agent_runs` or another feed).

Mapping run `agent` -> `AgentId` via `avatarForAgent()` (`WEB/lib/agent-avatars.ts:36-40`, ids at lines 3-15):
| agent_runs.agent | AgentId (avatar) | AGENT_TITLES text |
|---|---|---|
| kb_agent | knowledge | Knowledge base agent |
| ticket_agent | tickets | Ticketing & handoff agent |
| escalation_agent | tickets | (same avatar as ticket_agent) |
| order_agent | orders | Order management agent |
| order_approval | orders | (same avatar as order_agent) |
| invoice_agent | invoices | Invoice lookup agent |
| ecommerce_agent | ecommerce | E-commerce operations agent |
| subscription_agent | subscriptions | Subscriptions agent |
| booking_agent | bookings | Bookings agent |
Unused by runs: `support, assistant, hr, email` (service ids). `avatarForAgent` returns `null` for unknown names (then render the generic chip like `AgentCard` does, `RuntimeWorkspace.tsx:157-158,215-219`).
Careful: escalation_agent/ticket_agent share `tickets`, order_agent/order_approval share `orders`; "5 distinct agents" should be de-duplicated by AgentId (avatar), not by capability name, or two identical-looking avatars will appear.

AgentAvatar states (`WEB/lib/pixel-avatars.ts:81`, `components/console/fs/AgentAvatar.tsx`): `"idle" | "still" | "pending" | "rest" | "working" | "wake"`. Behaviour (`pixel-avatars.ts:121-128`): `still` and `pending` are dimmed and never animate; `rest` never animates and is not dimmed; `idle` loops 4 frames; `working` loops 8 frames (48 px grid only; 32 px falls back to idle loop); `wake` plays the 8 working frames once then holds. Everything falls back to frame 0 under reduced motion, hidden tab, offscreen. `AvatarSize` includes 32 and 48 (`gridForSize`).
The transition hook (`components/console/fs/useAvatarTransition.ts`, `lib/avatar-transition.ts`) is a switch-confirmation state machine: phases `idle|still|pending|wake|settling`, events `start|confirmed|failed|done`; wake lasts 1500 ms, settle 400 ms. It is driven by a capability SWITCH being toggled (see `AgentCard` lines 188-205), NOT by run outcomes; for a "recent agents" strip you would NOT use the hook, just pass a `state` prop to `AgentAvatar`.
Natural mapping if designed: last run `ok` -> `idle` (or `rest`); last run error -> `still` (dimmed) or a badge, since there is no error art state; there is no "working now" signal in runs (see terminal note), so `working`/`wake` would be decorative only. `rest` (not dimmed, no motion) is the neutral "has run before" look. Whatever is chosen is a new design decision; nothing in the code maps outcomes to avatar states today.

Can 5 distinct agents be derived from the most recent N runs? Not guaranteed. No distinct-agent aggregation exists server-side; the page size is 25 (UI) / max 200 (server). With 9 possible agents it is possible for one chatty agent (e.g. `kb_agent` on every customer turn, several runs per trace) to fill any N. Practical answer: request `limit=200` (max) once and de-duplicate by AgentId client-side; this gives 5 distinct avatars in nearly all real tenants but cannot be guaranteed (if fewer than 5 distinct agents appear in 200 runs, show fewer, which is correct for new tenants anyway). A guaranteed "last 5 distinct agents" needs a backend change, e.g. a `GET /console/v1/runs/recent-agents?limit=5` doing `SELECT DISTINCT ON (agent) ... ORDER BY agent, id DESC` then ordering by id. Note also that the avatar-level dedupe (tickets, orders pairs) means fewer than 5 avatars can exist in a tenant with only 5 capabilities of 3 avatar ids, etc.

## 1.D States

- Loading: tenant list "Loading tenants..." (option); runs "Loading runs..."; run detail "Loading detail..."; overview panel simply absent until loaded (no skeleton). No spinner/skeleton.
- Empty: two variants, see 1.A item 9; the "Last 24 hours" panel shows "0 recorded runs; 0 succeeded; 0 failed." and "Last run: No runs in this window." (overview is only hidden on error).
- Error: raw `ApiError.message` in a red Notice at the top of `RuntimeActivity` plus "Runtime data unavailable." in the list panel; polling continues and clears the error on the next success (`setError("")`). A failed poll sets `page` and `overview` to null (the list disappears, it does not keep stale rows). RunDetail instead keeps old data and appends "(previously loaded detail may be stale)".
- Permission / locked: Activity has NO package gate (server `/runs`, `/overview`, `/events` use only `Depends(tenant)`; nav has no `gate` entry for `activity`, `WEB/lib/navigation.ts:12-18`). Any role incl. `read_only` can see it. A tenant member passing someone else's `client_id` gets 403 (tested). Platform admin without `client_id` gets 400 "platform admin must select client_id explicitly" (UI prevents this by gating on the Tenant select).
- Read-only role: there are no write controls on this screen, so `read_only`, `tenant_staff`, `tenant_admin` all see exactly the same thing.
- Platform admin vs tenant member: platform admin gets the Tenant select and `client_id=` on every request, starts with nothing shown until a tenant is picked (or `?client_id=` is in the URL; note `selected` is initialised from the URL but the `<select>` only shows it once the tenant list has loaded). Tenant member: no select, data shown immediately, route under `/portal/activity`.
- Initial filter from URL `?agent=` (e.g. link from the dead Agents panel) pre-fills the filter and the field.

## 1.E Test ids and tests covering the screen

`data-testid` on THIS screen: none. (The `agent-card-*`, `agent-status-*`, `agent-switch-*`, `agent-subscribe-*`, `agent-pending-*`, `agent-last-change-*`, `agent-planned-*` ids belong to `AgentCard`, which lives in this file but is rendered on the Services page, see 1.F.) Tests locate by label/role/text.

Python browser:
- `tests/test_packaged_console.py::test_packaged_m2_browser_acceptance` (line 993; marked `slow`, needs docker + packaged image, so skipped by `make test`, runs in CI). Activity assertions at lines 1262-1357, wording that a redesign would break:
  - `get_by_label("Tenant").select_option(...)` (platform select labelled "Tenant")
  - `get_by_label("Agent name (optional)").fill("invoice_agent")`
  - `get_by_role("button", name="Filter / refresh", exact=True)` (clicked 3x)
  - run row located as a BUTTON with accessible name regex `#{id}\s+invoice_agent` (so row must remain a button whose name starts "#<id> <agent>"); absence asserted for `booking_agent` rows by the same regex (lines 1268-1274, 1338-1346, 1353-1357)
  - `heading name=f"Run #{invoice_run}"` exact (line 1280)
  - `get_by_text("browser_seed_alpha-invoice / invoice_agent / ok / 17 ms")` and later `"browser_poll_followup / invoice_agent / ok / 23 ms"` (RunDetail event line format `node / agent / status / N ms`; polled within 12 s, i.e. the 5 s poll must stay)
  - error path: an alert containing "synthetic transient failure" then `get_by_text("Runtime data unavailable.", exact=True)`, and the alert detaching after retry
  - API boundary: `/console/v1/runs?client_id=<other tenant>` from a read-only member must be 403; `/portal/activity` shows only own-tenant runs.
- Navigation labels (affect the nav, not the screen): `tests/test_console_shell_browser.py:127` (TENANT_LABELS includes "Assistant activity" in order) and `:297-299` (focus on link "Assistant activity" then Tab -> tooltip "Knowledge base", so ORDER of the two nav items is tested); `tests/test_services_browser.py:41,344`.
- No browser test covers the Activity page apart from the packaged M2 test.
Python API: `tests/test_console_sessions.py::test_m2_runtime_keyset_pagination_detail_redaction_and_overview` (line 709): overview counts, `limit`, `before_id`/`next_before_id`, `agent` filter, 404/422 bounds, 403/400 tenant rules, no payload leakage. Any new filter/param must extend this.
Node (`WEB/tests/`):
- `identity-transport.test.mjs:188` "M2 management and activity pages require a cookie..." for `/activity` and `/portal/activity` (and `/agents`, `/portal/agents`); `proxy.ts` route allowlist must keep any NEW route added here.
- `agent-avatars.test.mjs` (capability -> AgentId map, lines 15-27; `lhdn_agent`/unknown -> null).
- `theme-literals.test.mjs:22` lists `components/console/RuntimeWorkspace.tsx` as an OWNED file: no raw colour literals (hex/rgb or hard-coded palette) allowed in it; use `--fs-*` tokens. (`KnowledgeWorkspace.tsx` is NOT in that list.)
- `nav-visibility.test.mjs`, `assistant-view.test.mjs:58,284` (tab key `activity` and label "Assistant activity" in `lib/assistant-view.ts:97`; the Python mirror is `csa/workspace/rules.py:170`). Renaming the nav label must change both and `tests/test_workspace_planner.py`.

## 1.F Hazards

- `RuntimeWorkspace.tsx` is shared: it exports `AgentCard` (lines 178-265), imported by `WEB/components/console/ServicesWorkspace.tsx:16` (rendered at ServicesWorkspace.tsx:350). `AgentCard` is covered by `tests/test_agents_card_browser.py` and `tests/test_services_redesign_browser.py` (testids above; agent names like "Knowledge base agent"). Do not move or rename it casually; a redesign of Activity should leave the `AgentCard` export untouched or move it in a deliberate, separately-reviewed step.
- The file also holds `AgentManagement` (lines 67-155, `mode="agents"`), which is DEAD: `app/(admin)/agents/page.tsx` and `app/portal/agents/page.tsx` just `redirect("/services")` / `redirect("/portal/services")`, and `app/(admin)/agents/[name]/page.tsx` is `notFound()`. Nothing renders `mode="agents"`. It contains the only links to `/activity?agent=` (line 141) and to the KB page ("Open knowledge base", line 144), and technical words ("Failure guard", "Company switch", "circuit"). Safe to delete only if the owner agrees (it owns the unreachable `?agent=` deep link too). The `AgentCard` text itself ("Failure guard", "Recent failures", "Company switch", `connectionText(circuit_state)` e.g. "Paused after repeated failures", "Embeddings: {embedding_model}") is the technical vocabulary on the Services screen, outside this task.
- Hard-coded `limit=25`, 5 s polling in two nested effects, `before`/`filter`/`revision` state: the effect re-subscribes on `revision` (the form submit bumps it to force a refetch). Keep the abort/timer cleanup pattern when changing it.
- The component is 382 lines holding three behaviours (tenant picker, agent cards, activity); a redesign will probably want to split `RuntimeActivity`/`RunDetail` into their own file.
- `useAvatarTransition` and the AgentAvatar sprite data (`lib/pixel-avatars-data.ts`) are shared by Services/Inbox/Chat/Overview; do not change them for this screen.
- Files owned by others per memory: Overview/shell/assistant dock belong to the console-redesign track; the HR programme owns 0520-0529 migrations. A backend change for runs filters is in `csa/console/` + `db/sql/console/` (no HR/WhatsApp overlap) but it would need its own migration number if an index is added.

---------------------------------------------------------------------
# SCREEN 2: KNOWLEDGE BASE

Component: `WEB/components/console/KnowledgeWorkspace.tsx` (208 lines, two components `KnowledgeWorkspace` and inner `Knowledge`; imports only `lib/api`, `lib/identity-contracts`, `lib/page-context`, `./ui`).

## 2.A Layout today (on-screen order, exact wording)

`KnowledgeWorkspace` (lines 10-40):
1. `usePublishContext({ tab: "knowledge_base" })` (line 11): tells the assistant dock which tab is open; keep it.
2. h1 "Knowledge base" + description "Tenant documents, original sources, ingestion jobs and unanswered questions." (line 28).
3. Error Notice for the tenant-list failure (line 29).
4. PLATFORM ADMIN: label "Tenant" (`htmlFor="kb-tenant"`) + select (`id="kb-tenant"`), option "Select a tenant" / "Loading tenants..." (lines 30-36).
5. No tenant: Notice "Select a tenant to continue." (line 38).

`Knowledge` (lines 44-208), inside a vertical stack:
6. Notices (lines 126-128): `error` (red; action failures), `loadError` (red; poll failures), `message` (info; action results).
7. Button "Refresh knowledge base" (line 129).
8. Info Notice (lines 130-131): "Polling every 5 seconds. Lists show the latest 50 documents/gaps, 20 jobs and 100 sources. Uploads need configured storage and an ingestion worker. A queued job is not proof the worker is running."
9. While `data` is null: Notice "Loading knowledge base..." or, when loadError, "KB state unavailable. Refresh to retry." (line 132).
10. Panel "Embedding compatibility" (133-140) - TECHNICAL: `<p>{agent.detail}</p>` (server text, e.g. "Model-compatible KB retrieval; no live TEI probe." plus possibly "Dev lexical hash embeddings only; not semantic retrieval."), then "Model: {embedding_model ?? "Not configured"}. Compatible chunks: {n ?? "Unknown"}. Incompatible chunks: {n ?? "Unknown"}. No legacy FAQ files are imported automatically." Then, when not writable: "Writes require both KB subscription and tenant switch on." (writer role, capability not active) or "Read-only access: upload, deletion and gap resolution are unavailable." (read_only role).
11. Panel "Upload sources" (141-156), only if `canWrite`: label "One to three PDF, DOCX or CSV files, at most 10 MB each" for `<input id="kb-files" type="file" multiple accept=".pdf,.docx,.csv">` (disabled when busy or not writable); client validation error "Select one to three non-empty PDF, DOCX or CSV files, at most 10 MB each."; selected file names line; button "Queue upload / retry" (becomes "Working..." while busy; disabled until files chosen).
    - After success: "Queued job {job_id}. Queued is not yet searchable." (or "Existing job ..." when duplicate). After failure: "{message}. Retry keeps the same idempotency key; do not reselect files for an uncertain result." (technical wording: idempotency key).
12. Panel "Confirm deletion" (157-163), only when a delete is pending: "Delete {name}? Deleting a source with a linked document removes that document, its searchable chunks and every duplicate source linked to it. An already-authorized download may finish." Buttons "Confirm delete", "Cancel". After deletion: "Deleted. Queued work cannot recreate this source or its deleted content."
13. Panel "Ingestion jobs" (164-175): empty "No recorded jobs." Each job: bold `{job.id}: {job.status} (attempts: {attempts})` (UUID + raw status `queued|done|partial|failed` + attempt count - TECHNICAL), file names joined by commas, `job.error` in red (class `text-red-800`), then per-result lines `{file}: {status}[, version N][, N chunks][ — error]`.
14. Panel "Original sources" (176-187): button "Reconcile orphaned storage" (canWrite only; result: "Storage reconciled: N orphaned/deleted/partial files removed. Active sources are retained." - TECHNICAL); empty "No uploaded sources. Text and legacy documents may have no downloadable source."; each file: bold name, then "{media_type}, {size_bytes} bytes. Document {document_id} | No linked document yet." (raw MIME and UUID - TECHNICAL), link "Download original" (plain `<a href=.../kb/files/{id}/download?...>`), button "Delete source" (canWrite; disabled if !writable).
15. Panel "Documents and versions" (188-198): empty "No ingested documents."; each: h3 title, "Version {n}, active|superseded, {chunk_count} chunks. Model: {embed_model ?? "Unlabelled"}. {category ?? "Uncategorised"} / {language} / {kind}." (TECHNICAL: chunks, embedding model, kind), "Source: {file_name ?? source_uri ?? "Text / legacy source"}", button "Delete document" (canWrite).
16. Panel "Unanswered questions" (199-205): empty "No recorded unresolved gaps."; each: the question text, "{hits} hits. Last seen: {toLocaleString}", button "Mark resolved" (canWrite).

No `data-testid` anywhere on this screen.

Technical to hide/translate in a redesign: embedding model name, compatible/incompatible chunks, chunk counts, "version/superseded", MIME type, byte size, UUIDs (job id, document id), job status words and attempts, idempotency-key sentence, "Reconcile orphaned storage", "ingestion worker"/"storage configured" notice, "Polling every 5 seconds", the list-size sentence, `kind`/`language`/`category` slashes.

## 2.B Data

All via `api()`; platform admin adds `client_id=<uuid>` (the `query` string, KnowledgeWorkspace.tsx:37), tenant member none. Every refresh fires FIVE GETs in one Promise.all (lines 60-65), every 5000 ms (line 74, re-armed even after an error), plus on `revision` bumps:

| Call | Query | Response | Server limit (UI uses default) | Order |
|---|---|---|---|---|
| `GET /console/v1/agents` | `client_id` | `Agent[]`; UI only takes `name === "kb_agent"`; if absent, throws "KB capability metadata is unavailable." | n/a | name |
| `GET /console/v1/kb/files` | | `KbFile[] {id,name,media_type,size_bytes,document_id\|null,created_at}` | `limit` 1-100, default 100 (UI relies on default) | `created_at DESC, id DESC`, only `deleted_at IS NULL` (`db/sql/kb/files_list.sql`) |
| `GET /console/v1/kb/documents` | | `KbDocument[] {id,title,source_uri\|null,category\|null,language,version,is_active,file_name\|null,chunk_count,kind,embed_model\|null,created_at,updated_at}` | `limit` 1-100, default 50 | `created_at DESC` (`kb/list_documents.sql`; SQL has an `is_active` filter param that the route hard-wires to NULL = both) |
| `GET /console/v1/kb/ingest-jobs` | | `KbJob[] {id,source,status,files[{name,file_id}],attempts,error,meta.results[{file,status,error,document_id,chunks,version}],created_at,started_at,finished_at}` | `limit` 1-100, default 20 | `created_at DESC` |
| `GET /console/v1/kb/gaps` | | `KbGap[] {id,query,hits,first_seen,last_seen,resolved}` | `limit` 1-100, default 50 | `hits DESC, last_seen DESC`; route hard-wires `resolved=False` (unresolved only) |
| `GET /console/v1/admin/clients` | | `Client[]` | | platform admin only, once |

Mutations (all require session + CSRF token fetched via `GET /console/v1/auth/csrf`, handled in `api()`):
- `POST /console/v1/kb/uploads` multipart field `files` (1-3), header `Idempotency-Key: <uuid>` (UI generates `crypto.randomUUID()` per selection) -> 202 `{job_id, duplicate_request}`. Limits: 10 MB each, non-empty, pdf/docx/csv; 503 "KB upload storage is not configured" if no storage root.
- `DELETE /console/v1/kb/files/{id}` (204) and `DELETE /console/v1/kb/documents/{id}` (204); `POST /console/v1/kb/gaps/{id}/resolve` -> `{status, resolved_id}`; `POST /console/v1/kb/storage/reconcile` -> `{removed_files}`. All writes also need `kb_agent` allowed (409 "KB capability is missing or disabled" otherwise). `reconcile` is not behind `enabled` and shows for any writer.
- `GET /console/v1/kb/files/{id}/download` is a plain anchor navigation (cookie auth), streams the original with `Content-Disposition: attachment`. 404 if deleted, 503 "source file unavailable; storage repair required".
- (A JSON `POST /console/v1/kb/documents` text-ingest exists in `router.py:312` and `GET /kb/ingest-jobs/{id}` at `:355`, but this screen calls neither.)

Backend files: `csa/console/router.py:296-390` (documents, jobs, gaps), `csa/console/kb_uploads.py` (files, uploads, deletes, reconcile, download), `csa/console/kb_contracts.py` (JobOut: strips server paths, truncates errors to 500 chars), `csa/agents/kb/ingest_jobs.py` (job statuses `queued|done|partial|failed`), `db/sql/kb/*.sql`.

Pagination: NONE. Each list is a single capped fetch with no cursor, offset or total. The UI says so in its notice (50 documents/gaps, 20 jobs, 100 sources); anything beyond is invisible.

Server-side filters for KB:
| Filter | Exists? |
|---|---|
| Tenant | YES (`client_id` for platform admin) |
| Date range | NO on any KB list (only `limit`) -> NEEDS BACKEND CHANGE |
| Status | Jobs: NO (always all statuses). Documents: SQL supports `is_active` but the route does not expose it -> needs a one-line route change. Gaps: `resolved` hard-wired to false (cannot see resolved ones) -> needs route change |
| Search / title / text | NO |
| Category / language / kind | NO |
| Agent/capability | n/a |
No total counts either; document/chunk totals appear only as `compatible_chunks`/`incompatible_chunks` on the kb_agent entry of `/console/v1/agents` (sums over the model, from `console/kb_readiness.sql`).

## 2.C (Activity only) - n/a

## 2.D States

- Loading: "Loading tenants..." option; "Loading knowledge base..." notice (whole page body is replaced until all five calls succeed once). No skeleton.
- All-or-nothing load: one failing call in the Promise.all (any 403/5xx) hides EVERYTHING and shows the red `loadError` plus "KB state unavailable. Refresh to retry."; polling continues every 5 s and recovers by itself. A transient failure after a successful load also nulls `data` (page blanks to the "unavailable" notice rather than showing stale data).
- Empty: per-panel sentences listed in 2.A (no "empty state" component, no guidance on what to do first).
- Locked / package inactive: `documents`, `ingest-jobs`, `gaps` are gated by `package_gate("customer_support")` (`router.py:295`) -> 403 `package_inactive`; `kb/files` is NOT package-gated (only human session). So a tenant without active Customer Support gets the explained sentence "This area is not part of your organisation's active service packages. Ask your administrator to activate it in Services." in the red notice, with "KB state unavailable. Refresh to retry." Nav hides the entry for tenant members when `customer_support` is not active (`navigation.ts:13,28-33`), but platform admins always see it (they will hit 403 per tenant that lacks the package). Tested server-side: `tests/test_console_services.py:391`.
- Read-only role: `canWrite = me.role !== null && me.role !== "read_only"` (line 38). Read-only sees all lists and download links but no Upload panel, no Delete/Mark resolved/Reconcile buttons, and the sentence "Read-only access: upload, deletion and gap resolution are unavailable." (server also 403s non-GET for read_only in `access.authenticate`).
- Writer but KB switched off: `writable = canWrite && kb_agent.capability.active === true` (line 80); upload input and all action buttons are disabled with the sentence "Writes require both KB subscription and tenant switch on." No link to where to switch it on. Note a `tenant_staff` can write (server `writer` allows platform_admin, tenant_admin, tenant_staff); the capability toggles themselves need tenant_admin+ (and subscription = platform admin).
- Platform admin vs tenant member: as for Activity; platform admin sees tenant select, writes act on the selected tenant.
- Busy: all action buttons disabled while any action runs (`busy`).

## 2.E Test ids and tests covering the screen

`data-testid`: none on this screen.
- There is NO Python browser test of the KB page UI in this repo (grep for "Queue upload", "kb-files", "Refresh knowledge base", "Mark resolved", "Delete source", "Confirm delete", panel titles finds nothing in `tests/`). README (`WEB/README.md:141-154`) describes M3 manual acceptance only. So panel wording is not asserted by any browser test; a redesign can change wording freely on this page, but it should add the browser/regression coverage itself.
- Indirect wording/label dependencies: `tests/test_sandbox_chat_browser.py:44` - the sandbox chat copy says "To add documents, use Knowledge base." (a rename of the nav item would invalidate that sentence and test); nav labels "Knowledge base" in `tests/test_console_shell_browser.py:128,299` (order: Assistant activity then Knowledge base), `tests/test_services_browser.py:41,344`, `WEB/tests/nav-visibility.test.mjs:20-37` (hidden when Customer Support inactive; "stays locked" while suspended), `WEB/tests/assistant-view.test.mjs:53-74,277` ("Knowledge base is not available to you.", `tabHref("knowledge_base", ...)` -> `/portal/faq` or `/knowledge-base?client_id=...`, `tabForPath("/portal/faq")`), `lib/assistant-view.ts:92`, `csa/workspace/rules.py:153,165` (tab gate: needs `customer_support` active). `tests/test_services_redesign_browser.py:968,1019` use `agent-card-kb_agent` (AgentCard, different screen).
- `tests/test_agents_card_browser.py:288,515` and `test_services_redesign_browser.py:107` mention the title "Knowledge base agent" (AgentCard titles from `AGENT_TITLES`/catalogue "Knowledge base" in `csa/console/agents.py:25`).
- Node: `WEB/tests/identity-transport.test.mjs:214-227` (multipart upload keeps body, no Content-Type header, `Idempotency-Key`, `X-CSRF-Token`) and `:242` (routes `/knowledge-base`, `/portal/faq` need a session); `proxy.ts:20-21`.
- Python API: `tests/test_console_kb_uploads.py` (gaps listing/resolution/audit, 3-file upload, duplicates, roles/scoping/CSRF/machine, bounds, deletes, reconciliation, worker crash recovery), `tests/test_console_sessions.py:765` (KB execution cannot bypass disabled switch), `tests/test_console_services.py:391,413,457` (package gate for every role).

## 2.F Hazards

- `KnowledgeWorkspace.tsx` is not shared with other screens (only the two page files import it); safest of the two to redesign. It is not in the `theme-literals` OWNED list, but it uses raw Tailwind palette `text-red-800` (line 169) and `border-ink-200`; `ui.tsx` tokens (`--fs-danger` via `Notice`) are the sanctioned route if it gets added to OWNED.
- `ui.tsx` (`Panel`, `Notice`, `Heading`, `field`, `secondary`, `action`) is shared by ~all console screens; do not change its signatures.
- Fragile behaviours worth preserving: (1) the upload keeps the SAME `Idempotency-Key` across retries until files are re-picked or the upload succeeds (`selection.key`, lines 52,87-95); (2) the uncertain-retry wording explicitly tells the user not to reselect files; (3) download is a plain `<a href>` that relies on the cookie and `query` for platform admin; (4) polling every 5 s while a file input is open - `setData(null)` on any failure unmounts the whole panel stack including the chosen file list (the `selection` state lives in `Knowledge` so it survives, but the `<input>` is unmounted; `input.current.value` is no longer in sync after a blip); (5) `usePublishContext` must stay.
- Server gaps to flag for the owner: no pagination or search or date for documents/gaps/jobs; resolved gaps unreachable; `kb/files` not package-gated while the other lists are (inconsistent 403 vs 200 mix); `reconcile` available to any writer even when KB switch is off; gaps ordered by hits not recency.
- Messages `"Queued job {id}"` etc. tell the user about worker/ingestion internals; a plain-language rewrite should keep the truthfulness constraint ("queued is not searchable yet") the code is explicit about (AGENTS.md: surface errors, no silent fallbacks).

---------------------------------------------------------------------
## Quick answers to the specific questions

- Server-side date filter on Activity? NO. `/runs` takes only `before_id`, `agent`, `limit`, `client_id`; orders by `id DESC`; no total. Needs backend change (route + service + SQL, optional index).
- Server-side status filter on Activity? NO. Search? NO. Agent: exact match only.
- Server-side date filter on KB? NO (only `limit`). Status/search/category? NO. Needs backend change.
- Can "last 5 agents" be built today? Partly: fetch `/runs?limit=200`, map `agent` -> `avatarForAgent`, de-duplicate by AgentId, take first 5 by recency. Not guaranteed to yield 5; guaranteed version needs a new "recent distinct agents" endpoint. All run rows are terminal (`ok`/`error`; `partial` unused), so no "working now" state exists in this data. Only 9 capability names ever appear, mapping onto 7 avatar ids (knowledge, tickets, orders, invoices, ecommerce, subscriptions, bookings); `support, assistant, hr, email` never appear.
