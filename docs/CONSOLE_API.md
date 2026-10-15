# Console API contract (v1)

M0–M1 implementation, 2026-10-15: the retained Next.js presentation calls one CSAV2
gateway through same-origin `/console/v1/*`. The copied Python API is reference only.
See the [migration plan](./handoffs/2026-10-15-console-source-migration-plan.md) and
[frontend adoption instructions](../CSA_WebConsole/csa-console/web/README.md).

## Authentication, tenancy and settings

Human routes are mounted independently of `CONSOLE_API_TOKEN`. Configure
`IDENTITY_DATABASE_URL` using **csa_identity**, `CONSOLE_ALLOWED_ORIGIN` using the exact
web origin, and the frontend server-only `CSA_CONSOLE_API_ORIGIN` using the gateway
origin. `CONSOLE_COOKIE_SECURE=true` is the default; false is accepted only on loopback
dev/CI origins. HTTPS is required elsewhere. No proxy/forwarded headers establish identity.
`CONSOLE_SESSION_IDLE_SECONDS=1800` and `CONSOLE_SESSION_ABSOLUTE_SECONDS=43200` are
bounded and configurable. Missing identity configuration returns an explicit unavailable
response; configured pool failures fail startup/readiness. Both pools close at shutdown.

Migration **0270** creates users, sessions, memberships, tokens, throttle buckets and an
append-only audit ledger. The identity role has no application-domain access or role
memberships. Session/token tables allow global identity lookups only to that role;
membership and tenant audit operations set transaction-local user/tenant context. RLS is
enabled without FORCE. Domain mutations and their audit entries share a tenant transaction.

Provision the new login role by explicitly passing `CSA_IDENTITY_PASSWORD` (≥12 characters)
to `scripts.migrate` against an isolated database. It is optional: without it the new role
remains NOLOGIN. Re-running the migration runner with this variable deliberately provisions
or rotates the role password without altering the migration ledger. Keep values out of argv,
source and logs. Bootstrap the first admin deliberately:

```sh
NO_LOCAL_ENV=1 uv run python -m scripts.bootstrap_console_admin \
  --create-first-platform-admin --email admin@example.test --display-name 'Synthetic Admin'
```

The CLI reads only its explicit `IDENTITY_DATABASE_URL` environment value, prompts with
`getpass`, closes bootstrap after the first platform admin, and requires a password change.
No copied accounts, passwords or sessions are imported. Synthetic test identities are not
installed into an existing dev database.

Passwords support Unicode and use salted scrypt (N=131072, r=8, p=1), offloaded from the
event loop with bounded concurrency. Session secrets are 256-bit random opaque values;
only SHA-256 hashes persist.
The `csa_session` cookie is HttpOnly, host scoped, SameSite=Lax and Secure by default.
User disablement, session revocation, idle/absolute expiry and membership changes invalidate
access. Password changes revoke other sessions and rotate the current session and CSRF token.
Login throttling persists per normalized email and direct peer address; forwarding headers are
ignored. Invalid credentials use a generic response.

Every cookie mutation, including login, invite inspection/acceptance and reset requests,
requires an exact configured `Origin` plus double-submit `csa_csrf`/`X-CSRF-Token`; authenticated
CSRF is additionally bound to the session hash. Start with `GET /auth/csrf`. Responses are
`Cache-Control: no-store`; console validation errors omit raw input values. Forced password
change permits only identity inspection, CSRF, logout and password change before domain access.

Actors and roles come from verified sessions. Browser `X-Client-Id`, `X-Actor-Id`,
`X-Actor-Role`, `X-User-Id` and `X-Role` are rejected. An initial session selects its first
active membership; membership switching is deferred. Tenant users cannot select a different
`client_id`. Platform admins must explicitly select an existing tenant using `?client_id=UUID`
for tenant reads/mutations; they cannot invent an actor or grant platform status via JSON.

Machine access is separate: optional `CONSOLE_API_TOKEN` (≥32 chars), bearer authentication,
`X-Client-Id`, and `CONSOLE_MACHINE_CLIENT_IDS` (JSON UUID list). It permits existing domain
**reads only**, for allowlisted tenants. It cannot administer humans, onboard tenants, or
mutate capabilities/KB. Mixed bearer/cookie domain requests are rejected. Never expose the
machine token to browsers. Empty allowlist grants no tenant access.

## Implemented M1 lifecycle and onboarding

All paths below have prefix `/console/v1`. Models reject extra fields. Login/me/accept/change
return `{user:{id,email,display_name,is_platform_admin,must_change_password}, memberships:
[{client_id,role}], active_client_id, role, csrf_token}`.

| Method/path | Body or query | Permission/result |
|---|---|---|
| GET `/auth/csrf` | — | Obtain/refresh CSRF; recover from stale session cookies |
| POST `/auth/login` | `{email,password}` | Verified session; generic invalid-credential error |
| GET `/auth/me` | — | Verified identity, current roles/memberships |
| POST `/auth/logout` | — | Revoke current session, clear cookies; 204 |
| POST `/auth/password/change` | `{current_password,new_password}` | Change own password (12–1024 chars), rotate session |
| POST `/auth/password/reset` | `{email}` | Generic 202 directing user to admin; no account disclosure |
| POST `/invites/inspect` | `{token}` | Valid invite/reset metadata; never a token-bearing API URL |
| POST `/invites/accept` | `{token,password,display_name?}` | Single-use acceptance, membership/session creation |
| GET/POST `/admin/clients` | POST `{slug,name,timezone?}` | Platform admin; list (up to 200) / audited creation |
| GET `/profile` | Platform selection if applicable | Verified tenant profile |
| POST `/admin/invites` | `{email,display_name,client_id,role}` | Platform admin; issue manual-delivery invitation |
| POST `/team/invites` | Same | Selected tenant admin/platform; no platform role grants |
| GET `/admin/invites` | `client_id=UUID` | Authorized tenant admin/platform; pending/used/revoked/expiry metadata |
| POST `/admin/invites/{id}/renew` | — | Revoke old pending token, issue replacement; same scoped authorization |
| DELETE `/admin/invites/{id}` | — | Revoke invitation; reset-token revocation requires platform admin |
| GET `/team` | `client_id=UUID` | Selected tenant admin/platform; member list |
| PATCH `/team/{user_id}` | query `client_id`; `{role?,is_enabled?}` | Scoped member change; revoke affected tenant sessions; protect last admin |
| GET `/admin/users` | bounded `limit` | Platform admin; safe user metadata |
| PATCH `/admin/users/{id}` | `{is_enabled}` | Platform admin; disable revokes sessions/tokens; protect last admins |
| POST `/admin/users/{id}/reset` | — | Platform admin; revoke sessions/tokens, force change, issue reset |
| POST `/admin/users/{id}/revoke-sessions` | — | Platform admin; audited revocation |
| GET `/admin/audit` | optional `client_id`, bounded `limit` | Platform admin; global events or selected tenant events |

Roles are `platform_admin`, `tenant_admin`, `tenant_staff`, `read_only`. Tenant admins can
manage their tenant memberships and client switches, staff can perform authorized tenant
writes, and read-only users cannot mutate. Membership changes and disablement protect the
last active tenant administrator; platform disablement also protects the last platform admin.

Invitations/reset tokens are hashed, expire after 48 hours, and are consumed under locks.
Raw tokens are returned once to the authorized issuer for **manual delivery**. The UI uses
`/invite#token=...` and immediately clears the fragment before submitting the token in JSON.
There is no email provider integration. Existing credentialed accounts cannot be overwritten
through invitations; cross-tenant account attachment and membership switching remain deferred.
Admin-issued password resets require an account that has already set its password.
A pending invitation returns 409 without revoking its token; renew or accept that invitation
instead. This prevents reset acceptance from bypassing the membership-creation step.

Tenant creation atomically writes the tenant, nine disabled registry rows with current
Pydantic input schemas, and an actor-attributed audit event. It starts no channel, provider,
worker or external operation. Deliberate subscription/tenant enablement is separate.

## Endpoints
### `GET /console/v1/traces?limit=50&before=<ISO time>`
One row per customer turn, newest first. Page by passing the last `started_at` as `before`.
```json
{"trace_id":"…","conversation_id":"…","started_at":"…","finished_at":"…","steps":14,"had_error":false,
 "route":"plan_args","intent":"order","gate_decision":"send","agents":["order_agent"]}
```
`gate_decision`: `send` (sent), `approve` (held for staff), `reject` (shadow mode, nothing sent).

### `GET /console/v1/events?trace_id=<uuid>&after_id=0&limit=100`
Ordered timeline steps. Poll for live updates by passing the largest `id` you have as `after_id`
(every 1–2 s while a trace is open). `trace_id` is optional (omit for a tenant-wide live feed).
```json
{"id":812,"trace_id":"…","conversation_id":"…","node":"run_spoke","agent":"order_agent",
 "status":"ok","duration_ms":41,"summary":{"task_id":"t1","ok":true,"agent_status":"ok"},"created_at":"…"}
```

### Nodes and summary keys
| node | meaning | summary keys |
|---|---|---|
| load_ctx, guard | context loaded, safety check | – |
| signals | configured classifier judged the message | `intent`, `provider` (`llm`, `jev` or callback `policy`) |
| route | policy chose a path | `route` |
| fast_plan / plan_args | plan made | `intent`, `strategy`, `confidence`, `tasks` |
| run_spoke | an agent ran (`agent` set) | `task_id`, `ok`, `agent_status`, `error_code`, `requires_human` |
| collect | results merged into facts | – |
| observe | agent loop looked at the results and chose read-only follow-ups | `step`, `next_tasks`, `loop_stop` |
| handoff | escalation decision | `handoff_required`, `handoff_reason` |
| compose / verify | reply written / checked | `language`, `fact_count`, `verified`, `verify_issue_count` |
| gate | policy verdict | `decision`, `reason`, `risk` |
| hold / emit | reply held for staff / queued to send | `messages` |
| finalize / fail_safe | turn closed / safe fallback used | `error` |

**Agent loop.** After the first agents run, `observe` may start another round of read-only lookups
(for example product details after a search). Follow-up `run_spoke` events have task ids `s<step>-<n>-<agent>`,
so the console can draw rounds: `plan → run_spoke × n → observe (step 1) → run_spoke → observe (step 2)`.
`loop_stop` says why the loop ended: `done`, `rejected:not_read_only`, `rejected:duplicate`,
`rejected:over_budget` or `observe_failed:<error>`. Turns that are not LLM-planned have no `observe` event.

`status` is `error` when the step failed (`summary.error` has the code). Summaries never contain customer text
or secrets; they are codes, counts and names, so they are safe to render.
Semantic rejection details stay in turn state for regeneration; only their count appears on the timeline.
Classifier comparisons are persisted separately in `decision_log`; a console endpoint for those rows
is not implemented yet.

## Capabilities and specialised tabs (LG13)
- `GET /console/v1/capabilities`: each agent has `name`, `is_enabled` (the admin subscription), `client_enabled` (the client's own switch), `active` (true only when both are on), `circuit_state`, `fail_count`, `capability_kind`, and `updated_at`.
- `PATCH /console/v1/capabilities/{name}` with body `{"is_enabled"?: bool, "client_enabled"?: bool}`. A human platform admin controls subscriptions; a human tenant admin/platform admin controls tenant switches. Actor fields such as `changed_by` are rejected. Required safety capabilities cannot be disabled. Changes and before/after audit share one locked transaction. Runtime currently reads registry state directly, so no process-local cache can retain stale entitlement state.
- `GET /console/v1/tabs/specialized`: the tab catalogue (`lhdn_myinvois`, `ecommerce`, `knowledge_base`, `invoices`, `bookings`). Each tab has `is_subscribed`, `is_active`, and `metrics`. Show a tab only when `is_active` is true.

## Knowledge base (LG13)
- `GET /console/v1/kb/documents?limit=50` lists documents with `version`, `is_active`, `file_name`, `chunk_count`, `kind`, and `embed_model`.
- `POST /console/v1/kb/documents` adds plain text. Body: `{title, content, category?, language?, source_uri?}`. Returns 503 when the embedding service is down.
- `DELETE /console/v1/kb/documents/{id}` deletes a document.
- File upload is deferred to M3. The adopted frontend has no database connection or file-ingestion shortcut. The existing worker-side ingestion contract remains available to separately authorized synthetic fixtures/internal services.
- `GET /console/v1/kb/ingest-jobs?limit=20` and `GET /console/v1/kb/ingest-jobs/{id}` return the job `status` (`queued`, `processing`, `done`, `partial` or `failed`) and per-file `meta.results`.
- `GET /console/v1/kb/gaps` lists questions the KB could not answer. `POST /console/v1/kb/gaps/{id}/resolve` marks one resolved; it does not accept an answer text yet.

## Invoices (LG13)
- `GET /console/v1/invoices?limit=50` lists invoices.
- `GET /console/v1/invoices/{id}/download-link` returns **501** until M6 supplies a real artifact-serving path. The placeholder signing-key fallback is removed. The adopted UI does not offer downloads.

## OpenAPI
The gateway serves `/openapi.json` and `/docs` only outside production; Redoc is disabled.
The safe static [console OpenAPI snapshot](./console_openapi.json) contains console contracts
only and is exported without settings, DB connections or providers:

```sh
NO_LOCAL_ENV=1 uv run python -m scripts.export_console_openapi
```

Pydantic is authoritative. The first slice has explicit typed TypeScript adapters in
`web/lib/identity-contracts.ts` and `identity-api.ts`, checked by TypeScript and transport
tests. Regenerate/review this snapshot with contract changes; automated TypeScript generation
from this versioned artifact is the follow-on strategy rather than fetching machine-protected
runtime OpenAPI during builds. Do not infer authentication from a successful schema build.

## Not built yet (planned, see LG15)
Wave 2/3 preparation libraries are now available under `csa/agents/staged/` (2026-10-15).
Booking/subscription customer agents have local reads and unexecuted requests. Staff email,
web-research and invoice-draft preparation are **not HTTP endpoints**. A future authenticated
adapter must construct `StaffActor` from verified identity, never from user-supplied request
fields, and supply the tenant/contact context. No console source is needed for these libraries;
it is required to map existing UI calls and finish the actual turnover.

Server-sent live stream, conversations/messages read API, takeover/release/human reply, approvals queue and decide endpoints, policy and
agent settings editing, channel/integration connect, notification targets, DLQ/runs,
usage/billing, and a KB upload endpoint. M2–M8 are not completed by this identity slice.
Only M1 frontend pages are adopted; all project/PM/terminal/Hermes/diagram/workroom/voice and
unmigrated business pages return 404. No legacy API/n8n rewrite remains active.
