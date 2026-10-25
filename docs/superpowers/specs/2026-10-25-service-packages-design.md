# Service packages design — 2026-10-25

Status: design approved in conversation with the owner on 2026-10-25; implementation
plan follows separately. First of three related projects: (1) service packages,
(2) cross-tab workspace assistant, (3) HR completeness. This document covers (1) only.

## Intent

Fourstacks sells CSA to clients as a menu of automation services. A client chooses
the services they want, the platform owner activates them, and each active service
brings its own console tab and its agents. Today the console exposes a flat list of
nine agents with two switches each, and the sidebar shows HR, Email, Business and
Inbox to every non-read-only user regardless of what the tenant has switched on.
Clients cannot see what they have bought, cannot ask for more, and the owner has no
record of why a switch is on.

## Goals

- Group the existing agents and tabs into named packages a client can understand.
- Give platform admins a one-step, audited activation per package with a free-text
  commercial reference (quote or contract number).
- Let tenant admins see their active and available packages and request one.
- Show tabs only for active packages, and enforce the same rule server-side.
- Leave agent enforcement exactly as it is: agents still obey the existing switches.
- Keep a clean attachment point for a future billing module.

## Non-goals

- Prices, invoices, payment state, dunning or any billing logic.
- Changing how any agent runs, what it may do, or the HR grant model.
- Editing package contents from the UI; the catalogue is code.
- The floating assistant (project 2) or HR scope expansion (project 3).

## Anti-goals

- A tab hidden in the sidebar but still reachable and functional by URL. Hiding is not
  authorization; every package-gated surface must refuse server-side when inactive.
- Package state and agent switches silently disagreeing.
- A package that looks sellable while its agents are `not_implemented`.
- Weakening the rule that required safety capabilities (escalation, order approval)
  can never be disabled.

## Constraints

From `AGENTS.md`: psycopg 3 async only; Pydantic contracts; SQL under `db/sql/`
parameterized; additive ordered migrations with staged (not forced) RLS; only
`csa/graph/` imports LangGraph; exactly pinned dependencies; surface errors, no
silent fallbacks; `make lint test` and the disposable reset test before done.

Existing switch systems the package layer sits above, none of which change:

| System | Table | Who controls it today |
| --- | --- | --- |
| Customer agents | `capability_registry.is_enabled` (platform subscription) and `client_enabled` (tenant switch) | platform admin / tenant admin via `PATCH /console/v1/capabilities/{name}` |
| HR modules | `hr_agent_settings` rows `hr_directory`, `hr_training`, `hr_recruitment`, `hr_attendance`…, `hr_assistant` | the database-recorded HR client owner |
| Staff email | `email_settings.enabled` | tenant admin through a SECURITY DEFINER function |

Next free migration number is `0450`. Gateway readiness must move to `0450`.

## Approaches considered

- **A. Catalogue in code only, no persistence.** Activation flips member switches as a
  group; a package is "active" when all its switches are on. Rejected: no activation
  record or reference, no request flow, ambiguous half-toggled states.
- **B. Catalogue in code, activation records in the database.** Chosen. One
  tenant-scoped table records package state, actor, time and reference. Activation is
  one audited transaction that writes the record and flips the member switches.
  Enforcement stays with the switches.
- **C. Catalogue, contents and subscriptions all in the database, UI-editable.**
  Rejected for now: large, overlaps the deferred billing module, and lets package
  contents drift from the agents that exist in code.

## Decisions made with the owner

1. "Subscribe" means activation of a package, not billing. Billing is a later module.
2. **Customer Support** is the base package and the only one touching the client's
   customers. Orders and Products, Subscriptions, Bookings, Marketplaces, Customer
   Voice and E-invoicing are add-ons that require Customer Support.
3. **HR** and **Staff Email** are standalone packages; a client may take either alone.
4. The **workspace assistant** is a console feature, not a package.
5. Terminology: what clients buy from Fourstacks are **services** or **packages**.
   **Subscriptions** keeps its existing meaning: plans a client sells to its own
   customers.
6. Tenant admins can **request** a package; platform admins **activate** it.
7. People-aware features (project 2) require the HR package; otherwise the assistant
   states the information is not part of the subscribed packages. Recorded here
   because it fixes HR as a standalone package with a directory.

## Design

### Package catalogue (code)

`csa/console/packages.py` defines an ordered tuple of packages. Each entry has:

- `key` (stable identifier, e.g. `customer_support`), `title`, one-sentence `summary`
- `kind`: `base`, `addon`, or `standalone`
- `requires`: package keys that must be active first (add-ons list `customer_support`)
- `capabilities`: `capability_registry` agent names the package turns on
- `hr_modules`: `hr_agent_settings` names it covers (HR only; informational, see below)
- `email`: whether it covers `email_settings` (Staff Email only)
- `tabs`: console navigation keys it unlocks
- `sellable`: an explicit boolean in the catalogue entry (builder-check call). A
  consistency test asserts it is false whenever a member capability is
  `not_implemented` in `csa/console/agents.py` CATALOG, and the catalogue sets it
  false for `marketplaces` while no adapters are wired. Unsellable packages show as
  "coming soon" and cannot be requested or activated

Proposed catalogue:

| Key | Kind | Capabilities | Tabs |
| --- | --- | --- | --- |
| `customer_support` | base | `kb_agent`, `ticket_agent`, `escalation_agent`, `order_approval` | Customer inbox, Knowledge base |
| `orders_products` | addon | `order_agent`, `invoice_agent` | Business management |
| `subscriptions` | addon | `subscription_agent` | Subscriptions (specialised) |
| `bookings` | addon | `booking_agent` | Bookings (specialised) |
| `marketplaces` | addon | `ecommerce_agent` | E-commerce (specialised); not sellable until adapters exist |
| `e_invoicing` | addon | `lhdn_agent` | LHDN (specialised); not sellable |
| `hr` | standalone | none in `capability_registry`; covers `hr_agent_settings` | People & HR |
| `staff_email` | standalone | none in `capability_registry`; covers `email_settings` | Email assistant |

A Customer Voice add-on is deliberately **not** in this catalogue (builder-check call):
Telegram voice-note transcription is part of ingress, and interactive voice has no
sellable surface yet. It is added when the interactive path ships.

`email_assistant` has **no** `capability_registry` row for any tenant (onboarding
provisions only the nine customer agents), so Staff Email is a no-capability package
like HR: gated by the package row plus the existing `email_settings.enabled` switch.

A consistency test asserts every CATALOG agent except `email_assistant` appears in
exactly one package, with `escalation_agent` and `order_approval` only in
`customer_support`, and that every `sellable` package has a sellable member set.

### Persistence

Migration `0450_console_service_packages.sql`, additive:

```
console_service_packages (
  client_id uuid NOT NULL REFERENCES clients(id),
  package text NOT NULL,
  status text NOT NULL CHECK (status IN ('requested','active','suspended')),
  requested_by uuid REFERENCES console_users(id), requested_at timestamptz,
  activated_by uuid REFERENCES console_users(id), activated_at timestamptz,
  reference text CHECK (length(reference) <= 200),
  note text CHECK (length(note) <= 1000),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (client_id, package),
  CHECK ((status = 'active') = (activated_by IS NOT NULL AND activated_at IS NOT NULL) OR status = 'suspended')
)
```

Tenant RLS as the other console tables (enabled, not forced), `csa_app` SELECT/INSERT/
UPDATE, nothing for `csa_ro`/`csa_identity`. Package keys are validated against the
code catalogue in the service layer, not by a database CHECK, so adding a package is
a code change without a migration. A history of changes lives in the existing
`console_audit` rows (`service.requested`, `service.activated`, `service.suspended`,
each with before/after status and reference), not in a second table.

**Backfill (my call, reversible):** the migration inserts an `active` row with
`reference = 'backfill-0450'` and `activated_by` NULL-tolerant via `note` for every
tenant and package that has at least one member capability and whose member
capabilities are all `is_enabled`, so existing staging and dev tenants keep their
tabs. No-capability packages: `hr` is backfilled `active` where the tenant has any
`hr_agent_settings` row enabled; `staff_email` where `email_settings.enabled` is true.
A package with zero member rows is never backfilled by the "all enabled" rule. The builder may
instead choose an explicit `scripts/` backfill if the CHECK makes NULL `activated_by`
awkward; either way existing tenants must not lose tabs on deploy.

### Activation semantics

All in one transaction under the tenant context, with the existing
`console/capability_lock.sql` per member capability:

- **request** (tenant admin or platform admin): upsert `requested` unless already
  `active`. No switch changes. Audit `service.requested`.
- **activate** (platform admin, body `{reference, note?}`): requires every `requires`
  package to be `active`; sets row `active`; for each member capability sets
  `is_enabled = true` and `client_enabled = true` through the same SQL
  `toggle_capability` uses, so `client_enabled_changed_at` and the `capability.updated`
  audit fire exactly as today; audit `service.activated`. The tenant admin may still
  switch individual agents off afterwards; that does not change package status.
- **suspend** (platform admin): sets row `suspended`; sets `is_enabled = false` for
  member capabilities that are **not** members of another `active` package and are
  **not** in `REQUIRED`; refuses if a dependent add-on is still `active`
  (suspend add-ons first). Audit `service.suspended`.
- **HR and Staff Email switches are not flipped by activation (my call).** Their
  existing owners keep control: the HR client owner enables HR modules, the tenant
  admin enables email. Activation unlocks the tab and the server gate. For Staff
  Email the Services tab shows the `email_settings.enabled` state (readable under
  the tenant context). For HR it shows static guidance only ("HR modules are
  enabled by your HR owner inside the HR tab"), because `hr_agent_settings` is
  readable only inside an HR session and this project does not change the HR grant
  model (builder-check call). Reason: both switches have deliberate SECURITY DEFINER
  authority checks that a platform admin must not bypass.

### Drift check

`GET /console/v1/services` returns, per package, `status`, `consistent` and
`inconsistencies` (member capabilities whose `is_enabled` disagrees with the package
state). Readiness does not fail on drift; it is surfaced to platform admins in the
Services tab. The activation transaction is the only writer of both sides.

### API (console router, human sessions only, no machine access)

- `GET /console/v1/services`: catalogue joined with tenant state. Fields per package:
  key, title, summary, kind, requires, sellable, status (`available` when no row),
  reference, requested/activated actor display names and times, member agents
  (reusing `AgentOut`), tabs, `inner_switch` state for Staff Email only, `consistent`,
  `can_request`, `can_activate`, `can_suspend` (role-derived; machine principals false).
- `POST /console/v1/services/{key}/request`: tenant admin or platform admin. 200 with the
  package row; 409 if active; 422 unknown key or unsellable.
- `POST /console/v1/services/{key}/activate` body `{reference: str, note?: str}`:
  platform admin only; 409 if requirements inactive or unsellable.
- `POST /console/v1/services/{key}/suspend` body `{note?: str}`: platform admin only;
  409 if a dependent add-on is active.
- `GET /console/v1/services/active` returns `{"packages": [{key, status}]}` for the
  session's active tenant membership so the shell can build navigation in one round
  trip (planning call: the spec first placed this on `/me`, but `/auth/me` is served
  from the identity pool, which has no grant on the new table). Platform admins have no active tenant
  at the shell level (each admin page selects `?client_id=` itself), so for them
  `packages` is empty and **the shell shows every nav item** (builder-check call);
  the tenant pages and APIs they open still enforce that tenant's package state and
  show the Services tab's per-tenant view. Package gating of navigation is therefore a
  tenant-member experience; platform-admin experience is gated at the page and API.
- CSRF and `_no_store` as the rest of the console. OpenAPI snapshot regenerated and
  `docs/CONSOLE_API.md` gains a "Services and packages" section.

### Server-side gating

Nav hiding is presentation; these are the authorization changes:

- HR API (`csa/hr/api.py`) and Email API (`csa/email/api.py`): after the existing
  tenant and role checks, require the `hr` / `staff_email` package `active` for the
  tenant, else 403 `package_inactive`. Platform admins are not exempt.
- Specialised tab catalogue (`/console/v1/tabs/specialized`) already depends on the
  capability subscriptions and gains no new check.
- Inbox API (`csa/console/inbox.py`) and Business API (`csa/console/business.py`)
  check role only today (builder-check finding). Add package gates: Inbox requires
  `customer_support` active; Business requires `orders_products` **or**
  `marketplaces` active, because the Business pages also show stores and shop
  bindings. 403 `package_inactive` otherwise (my call).
- Knowledge base API (reads, downloads and writes under `/console/v1/kb/*`) requires
  `customer_support` active, for every role including read-only (my call). The KB
  tab belongs to Customer Support; a tenant without that package has no knowledge
  base to read. Read-only users keep their download permission within an active
  package.
- Sandbox chat, audit, team and settings are console features and are never
  package-gated.

### Console UI

- Sidebar (`components/AppShell.tsx`): the **Agents** item becomes **Services**. Items
  for Inbox, Knowledge base, Business, People & HR and Email render only when their
  package is `active` in `me.packages` and the role rule allows. Read-only users keep
  today's rule (no Inbox, Email, Business, Workspace). Specialised tabs continue to
  come from `/tabs/specialized`.
- **Services tab** (`/services`, `/portal/services`): three groups, Base, Add-ons,
  Standalone. Each card: title, summary, status badge (Available, Requested, Active,
  Suspended, Coming soon), reference and activation time when active, expandable list
  of member agents reusing the existing agent detail rows and the per-agent switches,
  and the action for the role: **Request** (tenant admin), **Activate with reference**
  and **Suspend** (platform admin, confirmation card). Inner-switch hint for HR and
  Email. Drift warning for platform admins.
- The per-agent pages under `/agents/{name}` stay reachable from the card expansion;
  the top-level `/agents` route redirects to `/services`.
- A browser acceptance in `tests/` mounts the real shell with a synthetic `/me` and
  `/services` transport and checks nav visibility per package state and role, the
  request and activate flows, and that a direct navigation to a hidden tab shows the
  server's 403 rather than content.

### Tests

Disposable PostgreSQL via Testcontainers, `NO_LOCAL_ENV=1 ENV=ci`, synthetic fixtures:

- Catalogue consistency (every CATALOG agent in exactly one package; required
  capabilities only in `customer_support`; add-ons require `customer_support`).
- Migration applies from scratch and readiness expects `0450`
  (`tests/test_migrate.py`, `tests/test_hr_persistence.py`).
- Backfill produces `active` rows exactly for fully enabled packages.
- Request/activate/suspend permissions per role, machine principal and read-only.
- Activation flips member switches and writes both audit kinds; suspension spares
  shared and required capabilities; dependent add-on blocks suspension.
- Drift detection after a manual capability toggle.
- HR and Email APIs return 403 `package_inactive` when the package is not active.
- `/me.packages` content per role and selected tenant.
- Contract snapshot (`tests/test_console_contract_snapshot.py`) updated.
- Frontend isolated tests for nav mapping; browser acceptance as above.

### Documentation changes in the same change

- `docs/CONSOLE_API.md`: new "Services and packages" section; adjust the Capabilities
  section to say packages are the management surface and switches remain enforcement.
- `docs/PROJECT_OVERVIEW.md`: replace the flat agent description with the package map;
  fix the stale schema version.
- `docs/PROGRESS.md`: entry with evidence.
- `docs/ARCHITECTURE.md`: one paragraph on the package layer above the switches.
- `deploy/STAGING_PIPELINE.md` §6: remove the stale "Meranti is in shadow" sentence
  (PR #31 restores the activation record).

## Left to the builder

- Exact SQL file names under `db/sql/console/`, Pydantic model names, error strings.
- Whether `/me.packages` is computed in the identity service or joined in the router.
- Card layout and copy in the Services tab, within the existing design tokens.
- Whether the Inbox needs a new server check (see Server-side gating).
- Order of implementation slices; suggested: migration and catalogue, service layer
  and API with tests, `/me` and shell navigation, Services tab, HR/Email gates, docs.
