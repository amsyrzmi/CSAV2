# CSA Console presentation

The adopted frontend uses Next.js **16.3.8** and React **19.2.8**, with one CSAV2
backend. The copied Python API is reference source only. This package does not
import, mount or start it or any PM/Hermes/workroom workers.

Copied-repository cleanup removed obsolete parent Makefiles/compose launchers,
backup archives, OS/Python cache files and standalone debug/scratch scripts.
The checkout is tracked as ordinary files in the parent CSAV2 repository; nested
Git metadata and the former gitlink were removed. Installed frontend dependencies,
local environment files and legacy backend reference code remain excluded from Git;
the copied workflow export and drop snapshot are also excluded. The canonical deferred
PM reference pack is retained. Use the commands below, not the
old console setup or tunnel instructions. The authoritative state is
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

### Current isolated browser-test session (2026-10-15)

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

## M0–M3 adoption inventory

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

Projects, shared projects, PM boards/notes/planner/diagrams, terminal, Hermes and
workroom are inaccessible. System/chat and other feature-specific management views await their later
CSAV2 integration slices. Their pages are non-importing 404 stubs and the proxy
also rejects direct access, including the copied `/api/*` paths. Legacy components
remain as inert reference source, excluded from the typed/lint adoption boundary.
The adopted build has no old API URL or n8n dependency.

Reviewed source comprises `app/` (including non-importing deferred stubs),
`components/console/`, `components/AppShell.tsx`, the auth-card/password/input
controls, `lib/api.ts`, `lib/identity-*.ts`, `lib/server-identity.ts`,
`lib/upstream.ts`, `proxy.ts`, the tool configuration, synthetic transport tests,
and the supplied brand mark. The build-check script lists its exact file boundary.
The supplied `ui-guide/fourstacks-tokens.css` is imported before product styles and
scoped through `fs-ui`; the light canvas, blue actions, control boundaries, explicit
role labels and brand mark follow the detailed UI guide. Font family names use local/system fallbacks because font files
were not supplied, and no remote font fetch is needed to build.

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
