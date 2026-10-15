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
checking it is available**. No persistent frontend or gateway listener was started for M0–M1,
and no existing port, process, hostname, webhook or proxy was changed.

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

## M0–M1 adoption inventory

Active routes: `/login`, `/forgot-password`, `/invite`, `/change-password`, `/`,
`/settings/clients`, `/settings/team`, `/portal` and `/portal/team` (tenant admin
only). The shell shows only supported routes and resolves identity through CSAV2.
Auth and member operations do not depend on `CONSOLE_API_TOKEN`.

Projects, shared projects, PM boards/notes/planner/diagrams, terminal, Hermes and
workroom are inaccessible. Agent/activity/system/FAQ/chat views await their later
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
