# CSA Console presentation

The adopted frontend uses Next.js **16.3.8** and React **19.2.8**, with one CSAV2
backend. The copied Python API is reference source only. This package does not
import, mount or start it or any PM/Hermes/workroom workers.

Copied-repository cleanup removed obsolete parent Makefiles/compose launchers,
backup archives, OS/Python cache files and standalone debug/scratch scripts.
The checkout is tracked as ordinary files in the parent CSAV2 repository; nested
Git metadata and the former gitlink were removed. Installed frontend dependencies,
local environment files and legacy backend reference code remain excluded from Git.
The obsolete `CSA_WebConsole/Docs/` tree (old n8n workflows, a pre-migration
console snapshot and deferred PM design pack) was removed on 2026-10-20; none is
adopted source or active implementation guidance. Use the commands below, not old
console setup or tunnel instructions. The authoritative state is
[CSAV2 progress](../../../docs/PROGRESS.md) and the
[migration plan](../../../docs/handoffs/2026-10-15-console-source-migration-plan.md).

## Development configuration

Set **`CSA_CONSOLE_API_ORIGIN`** in the frontend process environment to the isolated
CSAV2 gateway origin, for example `http://127.0.0.1:18080`. It is server-only and
must be a bare HTTP(S) origin, without credentials, path or query. There is no
fallback origin. Browser requests use same-origin `/console/v1/*`; no browser
service token or actor/role/tenant headers are configured.

Use an explicitly selected, unused loopback port when starting a future isolated
frontend, for example `npm run dev -- --hostname 127.0.0.1 --port 13000` **only after
checking it is available**.

### Historical isolated browser-test session (2026-10-15)

This records the approved session at that date, not current service state or
authorization to start/restart these tasks.

VS Code tasks in the repository's `.vscode/tasks.json` start the gateway on
`127.0.0.1:18081` and frontend on `127.0.0.1:13000`. They are loopback-only;
forward port 13000 privately through VS Code and open **http://localhost:13000**.
The configured allowed Origin must match that address exactly. This local HTTP
session uses `CONSOLE_COOKIE_SECURE=false`; do not reuse that setting for public HTTPS.
The frontend task must run from this package directory so Next resolves its
TypeScript configuration imports correctly.

The gateway uses the existing isolated dev database on port **5434** and verifies
the domain/identity DSN roles before startup. Migrations 0250 through 0280 were
applied forward-only without a reset; the user set the identity password and
bootstrapped the first admin interactively. Login/password change and the admin,
tenant-list and member screens passed a user-confirmed browser smoke check.
M3 subsequently added **Console: isolated dev KB ingestion**, a guarded KB-only worker
for the synthetic M1 tenant, using the existing loopback TEI. No full worker, delivery
process, bot or public proxy was started. Gateway and KB tasks share the dedicated
`$HOME/.local/share/csav2-dev/kb-uploads` root via task-local overrides; no environment
file was edited. Use these tasks rather than generic launch commands that may lack
the override. Stop the corresponding task terminal with Ctrl-C when finished; do not restart
unrelated services. Current frontend tooling references the already installed
Node/npm wrappers under `/tmp/fourstacks-toolbin`; these host-local paths are not
a portable Node installation and may need replacement after temporary-file cleanup.

Do not copy any `.env` from the reference checkout. Do not use production identity
or domain data. Bootstrap a synthetic platform admin using the CSAV2 CLI and
create synthetic tenants/invitations through the adopted UI. Suggested fixture
emails are `platform-admin@example.test`, `tenant-admin@example.test`,
`staff@example.test` and `reader@example.test`; there is no shipped password.
Invitation/reset links are delivered manually in this slice. Public password reset
requests give a generic response; no email delivery is claimed.

## Reviewed install and verification

Before execution, `package.json` scripts and lockfile v3 were reviewed: the copied
scripts were only `next dev`, `next build`, `next start` and `eslint`. The lockfile
had one `hasInstallScript` package (`unrs-resolver`); its lifecycle script was not
executed. Existing versions were pinned exactly to their lockfile versions, with
no dependency upgrade. Install only using:

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm run check:isolated
```

The check copies only reviewed frontend source into a new `/tmp/csa-console-check-*`
directory, links the already installed dependencies, and runs synthetic transport
checks, ESLint, Next type generation, TypeScript and `next build --webpack`. It
passes a synthetic loopback origin and a minimal process environment; it neither
copies nor loads environment files, the old backend, nested Git metadata or backup
archives. It does not start a server or contact the configured backend. An installed
Node >=20.9 is required. M1 verification used Node 24.21.0.

The installed Next.js documentation for proxy, async cookies, rewrites and Turbopack
root boundaries was read before frontend edits. Direct commands `npm run lint`,
`npm run typecheck`, `npm test` and `npm run build` remain available; use the isolated
check to keep environment files outside Next's automatic environment loading.

CI uses `node scripts/check-isolated.mjs --install`: it installs the reviewed lock
inside a fresh allowlisted directory, with lifecycle scripts disabled, then runs the
same checks. Both modes remove their own temporary directory on success or failure.
`--context NEW_DIRECTORY` instead materializes the same allowlist for a container
build, without installing dependencies, linking local modules or reading environment
files. Do not build Docker directly from this checkout.

Release builds use Next standalone output and a server-only, origin-bound gateway
rewrite. The image launcher requires runtime `CSA_CONSOLE_API_ORIGIN` to match its
build value; changing it requires rebuilding. See the repository-only
[release packaging and inert staging proposal](../../../deploy/RELEASE.md).
No publishing, deployment, DNS or live provider capability is enabled.

## M0–M4 adoption inventory

Active routes: `/login`, `/forgot-password`, `/invite`, `/change-password`, `/`,
`/settings/clients`, `/settings/team`, `/portal` and `/portal/team` (tenant admin
only). The shell shows only supported routes and resolves identity through CSAV2.
Auth and member operations do not depend on `CONSOLE_API_TOKEN`.

M2 adds `/agents`, `/activity`, `/portal/agents` and `/portal/activity`. Platform
users select an existing tenant explicitly. Subscription and tenant switches are
separate; tenant admins cannot subscribe themselves, and staff/read-only users have
no toggle controls. Specialised inspection tabs use backend entitlement/implementation
metadata and retain setup-required/prepared-only labels. KB now links to the M3 screen;
other specialised tabs are not the future
booking, marketplace or billing management screens.

Activity polls scoped recorded-run metadata, the last-24-hour overview and incremental
trace events. No raw run payloads are shown; missing records are not provider health.
With explicit approval the isolated gateway was restarted to load M2. Platform
tenant subscriptions, Agents and Runtime activity load in the authenticated browser;
the user subsequently confirmed the guided tenant-admin/reader checks and cleanup.
No populated-run or real-provider browser result is claimed.

Packaged JavaScript-enabled M2 acceptance now exercises login, password change,
logout/session revocation, platform-admin and tenant-admin switches, reader
read-only behavior, tenant-scoped Runtime activity, polling, visible refresh
failure/recovery, and same-origin/no-egress behavior against synthetic data and
disposable packaged services. The focused check
`NO_LOCAL_ENV=1 ENV=ci uv run pytest -c pyproject.toml tests/test_packaged_console.py::test_packaged_m2_browser_acceptance -v --tb=short`
passed in an isolated run on 2026-10-20 (one existing Testcontainers deprecation
warning); a later sequential run with the preceding packaged identity test also
passed after isolating identity fixtures in the shared disposable database.
It does not establish live provider, worker, public-proxy, persistent-staging or
exact-hosted-browser behavior. CI installs Chromium and its system dependencies
before pytest; local runs need Playwright Chromium and its host libraries.

M3 adds `/knowledge-base` and `/portal/faq`: 1–3 multipart PDF/DOCX/CSV uploads
(10 MiB/file), stable retry idempotency keys, queued/processing/partial/failed job
status, original-file downloads, document versions, embedding compatibility and gaps.
Reader mutation controls are absent. Destructive actions require confirmation and
explain shared duplicate-source deletion. Explicit storage reconciliation recovers
UUID-pattern orphan/partial/deleted files without touching active sources or unknown names.
The transport keeps cookies/CSRF but lets the browser generate multipart boundaries.
Next's proxy buffer is 31 MiB, aligned with the authoritative streaming gateway limit.

M3's **forward-only 0280 migration** and scoped dev restarts were authorized and completed
without resetting the persistent database or bootstrapping accounts again. Platform-admin
browser upload of all three formats reached done through loopback TEI; original hashes,
duplicate suppression, confirmed deletion and reconciliation passed with audit evidence.
Select **M1 Browser Acceptance**: its KB switches are enabled and one synthetic DOCX/chunk
remains for testing. Tenant-admin/reader M3 browser replay is not yet independently checked;
HTTP integration tests cover those role gates. A queued job alone still proves neither
worker nor TEI health. Use synthetic files and
[the API rollout/recovery contract](../../../docs/CONSOLE_API.md#isolated-dev-rollout-prerequisites).

M4 adds `/chat` and `/portal/chat`, explicitly labelled **Sandbox assistant**.
Conversations are private to the verified account and tenant, with a separate sandbox
contact. Only knowledge and product-information reads are allowed by the runtime;
customer records, business mutations, staff escalation, callbacks/confirmations,
voice and attachments are unavailable. Reader memberships cannot create/send/archive.
This is not a customer inbox or an external messaging channel.

History polls every 2.5 seconds using stable turn IDs and older-page cursors.
Queued/processing/awaiting-delivery, held, privately available, failed and no-reply states
are distinct. Held drafts are not falsely displayed as delivered or automatically released.
HTTP requests time out after 15 seconds; uncertain sends keep the same in-memory key/text
for explicit retry, while reconnect only checks persisted history. A 90-second turn
deadline is a delay warning, not a delivery claim. After reload or leaving the tenant,
check persisted history before sending again. Archive is confirmed, audited hide and
does not delete history, uploads or accepted work.
See [the M4 API/safety contract](../../../docs/CONSOLE_API.md#private-sandbox-assistant-m4).
Forward migration 0290 and guarded dev restarts were separately approved and completed.
Functional acceptance, including reader controls/ownership, passes; the user accepted
M4 closeout with a documented laptop-dev performance exception, not green latency tests.
See [the closeout and M5 kickoff](../../../docs/handoffs/2026-10-16-m4-closeout-m5-start.md).
Future migrations/restarts still require scoped approval; an isolated build is not
live acceptance, and M5 must not expose owner-private sandbox history or held drafts.

M5 adds `/inbox` and `/portal/inbox` for Telegram customer conversations and reply
approvals. Only platform administrators with explicit tenant selection, tenant
administrators and tenant staff can read or act. Reader navigation is absent and
backend content reads are denied. Sandbox histories/holds remain private; customer web
and order decisions are deferred. Takeover pauses AI until release, human reply requires
takeover, and old turns stay suppressed after release. The queue supports source
navigation, approve/reject/edit, history pages and stale refresh. Outcomes distinguish
queued, delivered, suppressed, expired and already handled. Uncertain actions retain the
same in-memory key and payload for explicit retry; reconnect only refreshes. After
navigation/reload inspect history before resubmitting. Explicitly approved forward-only
0300 and coordinated guarded dev replacement are complete; platform-admin UI and
synthetic Telegram acceptance, live reader denial and representative tenant-admin/staff
browser replay are verified; automated authority evidence remains green.
Future rollouts require scoped approval. See [the M5 contract](../../../docs/CONSOLE_API.md#telegram-customer-inbox-m5-guarded-dev).

For a guarded dev production build, set `CSA_CONSOLE_API_ORIGIN` to the actual gateway
origin **at build time as well as start time**: Next.js rewrites are compiled into
the build. The isolated check script uses test port18080; the current guarded gateway
uses18081. Do not serve an unchanged test-origin build and expect a start-time override
to rewrite it. Keep environment files and legacy API code outside the isolated stage.

Projects, shared projects, PM boards/notes/planner/diagrams, terminal, Hermes and
workroom are inaccessible. System and other feature-specific management views await their later
CSAV2 integration slices. Their pages are non-importing 404 stubs and the proxy
also rejects direct access, including the copied `/api/*` paths. Legacy components
remain as inert reference source, excluded from the typed/lint adoption boundary.

M6A adds separate `/business` and `/portal/business` pages offline. Navigation and data
require human admin/staff roles; readers see an explicit permission notice and backend
reads deny them/machines. Platform administrators select a tenant; members use their
verified active tenant. Lists page local orders/tracking references and local products/
stock through `/console/v1/business`. Refresh is explicit, abortable and deadline-bounded;
tenant changes remount data state. Failures are visible, not converted to empty success.
Local empty states never claim the marketplace has no orders/listings; local timestamps
are not sync freshness. No listing/stock/refund/payment writes, connection checks or
provider calls are performed. Later features are explicitly unavailable. This is the
[first slice of the expanded M6 roadmap](../../../docs/handoffs/2026-10-15-console-source-migration-plan.md#m6--business-tabs-integrations-and-staff-preparation),
not a full seller platform or an approved replacement of the last approved M5 console.
The workspace also has an opt-in synthetic demo mode for presentations. It displays only
fictional sample listings and a local-only simulated create action; no tenant records or
TikTok API calls are used in demo mode. Changes remain in component memory and reset when
demo mode is exited or the page is reloaded. The persistent label states that no shop is
connected and nothing was published.
The latest read-only probe found no gateway/frontend listeners on18081/13000; the
historical acceptance above is not a current availability guarantee. M6 isolated frontend
validation passes **13 transport + 2 state tests**, lint/types/build and trace checks.
Backend full validation has one unchanged TCP latency failure; see current progress.
The adopted build has no old API URL or n8n dependency.

M6B's local-order continuation adds exact source/platform and optional store ID filters.
Store IDs require a source; supported source options reflect the local schema, not
connected adapters. Draft changes apply only on **Apply order filters**; the applied
scope is shown separately. Changing applied scope remounts orders at newest and aborts
obsolete reads; changing tenant resets both filters and record state. Inventory stays
tenant-wide, explicitly unaffected, until verified listing/stock mappings exist.
Synthetic frontend validation passes **14 transport + 2 state tests**, lint/types/build/
trace checks. Browser rendering/pagination/abort-race acceptance remains unverified:
the shared browser could not reach the temporary loopback fixture. That fixture was
stopped and removed, without changing forwarding or infrastructure.
Filter-slice backend full validation: **736 passed / 1 TCP latency failure** (p95 **235.195 ms**,
unchanged **<200 ms**), lint/disposable reset checks pass. Full validation is not green.

M6B also adds a **Local store records (not verified connections)** panel. Its staff-only
`/console/v1/business/stores` reads expose local record/platform/store IDs and recorded
expiry/update timestamps, never token contents, permission scopes or arbitrary metadata.
Authorization remains unverified, permissions unknown and provider reads unavailable;
future local expiry is not a connected/valid-token badge. Missing store IDs are explicit.
Platform/tenant changes reset store paging, which follows stable record IDs, not recency.
No connect/revoke/refresh-token controls or provider calls are wired. Isolated validation:
**15 transport + 2 state tests**, lint/types/production build/trace checks pass.
Store-slice backend full validation: **742 passed / 1 TCP latency failure** (p95 **200.161 ms**,
unchanged **<200 ms**); lint and disposable reset checks pass. Full validation is not green.
Later backend-only TikTok request corrections pass full lint and **749 tests**, including
unchanged standalone ASGI/TCP thresholds. Frontend source did not change in that slice;
the **15 transport + 2 state tests** build evidence above remains the latest frontend run.
Historical failures remain recorded; no new browser/live-provider acceptance is implied.

The subsequent staff lifecycle-status slice enriches store list/detail with
`connection:{status,live_integration:"unavailable"}` and displays explicit labels for
unverified, revoked/disconnected, expired, app-unavailable, missing binding/permissions
and locally recorded authorization. Even `authorization_recorded` means **not verified
live**: authorization/permission/dispatch authority stays unavailable. No tokens,
scope values, shop ciphers, app secrets or authorization-attempt secrets reach the browser.
Connect/Reconnect buttons are disabled without click handlers; the page explains the
Partner eligibility gate without assuming account registration or Sandbox access is enough.
Existing explicit refresh, platform filters, keyset paging and tenant remount/abort behavior
are unchanged. Isolated validation: **16 transport + 2 state tests**, frontend lint,
typegen/typecheck, production build and safe build traces pass. Backend targeted selection:
**30 passed** including lifecycle/role/tenant/contract/disposable reset/migration checks.
Full backend lint/tests **975 passed** (136 warnings), including disposable reset and
unchanged standalone ingress limits; historical latency failures remain recorded.
No service restart or live browser acceptance is claimed.

Reviewed source comprises `app/` (including non-importing deferred stubs),
`components/console/`, `components/AppShell.tsx`, the auth-card/password/input
controls, `lib/api.ts`, `lib/identity-*.ts`, `lib/server-identity.ts`,
`lib/upstream.ts`, `lib/sandbox-chat.ts`, `lib/business.ts`, `proxy.ts`, the tool configuration, synthetic transport/state tests,
and the supplied brand mark. The build-check script lists its exact file boundary.
The supplied `ui-guide/fourstacks-tokens.css` is imported before product styles and
scoped through `fs-ui`; the light canvas, blue actions, control boundaries, explicit
role labels and brand mark follow the detailed UI guide. Font family names use local/system fallbacks because font files
were not supplied, and no remote font fetch is needed to build.

## Staff HR adoption (source implementation, 2026-10-21)

`/hr` and `/portal/hr` are authenticated adopted pages backed by the gateway's
`/console/v1/hr` contract. They include explicitly granted directory/training,
local recruitment, attendance/leave and private payroll-review forms. HR owner
assignment and field/action/private grants are separate from console roles;
showing a page does not grant access. Workspace A1 remains a separate page and API.

The optional HR assistant sends submitted messages to the configured model only
when enabled; it does not supply private stored CV/payroll records to that model.
Local dictation fills a reviewable text box and never auto-submits. Unsupported
on-device speech fails explicitly with typed input still available; no cloud
speech fallback or automatic language-pack download is selected. This is not
the planned interactive LiveKit voice agent.

See [HR setup](../../../docs/hr/SETUP.md) and
[current validation](../../../docs/hr/PROGRESS.md). Isolated transport/build checks
do not establish authenticated browser/microphone acceptance or authorize
persistent migrations, worker activation, external sends or deployment.

## Identity contract

`lib/identity-contracts.ts` mirrors the authoritative Pydantic models in
`csa/console/contracts.py`. `lib/identity-api.ts` is a small typed client over the
same-origin API. All mutations obtain `/auth/csrf` first and send `X-CSRF-Token`;
the browser supplies the Origin and session cookies. Invitations/reset credentials
use `/invite#token=...`, are removed from the address bar and go only in POST bodies,
never API URL paths. Logout correctly accepts HTTP 204.

The published CSAV2 OpenAPI snapshot is the contract source for future automated
client generation. Keep the curated identity types synchronized with its Pydantic
schemas now; generate from the versioned static snapshot in CI when a generator is
introduced with an explicitly pinned dependency, rather than fetching a live or
production schema during builds. No schema fetch, browser secret or mock runtime
is required by this frontend.
