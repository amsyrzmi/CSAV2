# Service Packages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group the console's agents and tabs into named service packages that a platform admin activates per tenant with an audited commercial reference, that tenant admins can request, and that gate both navigation and the server-side APIs.

**Architecture:** A code-defined package catalogue (`csa/console/packages.py`) sits above the three existing switch systems (agent `capability_registry`, `hr_agent_settings`, `email_settings`). One new tenant-scoped table records package state; activation is one transaction that writes the record and flips member agent switches through the SQL `toggle_capability` already uses. A `require_package` dependency gates Inbox, Business, Knowledge base, HR and Email APIs. The Next.js shell builds navigation from the tenant's active packages and a Services tab replaces the flat Agents list.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 async, Pydantic v2, PostgreSQL 16 with staged RLS, pytest + Testcontainers, Next.js (App Router, `node --test` transport tests), Playwright browser acceptance.

**Spec:** `docs/superpowers/specs/2026-10-25-service-packages-design.md`

## Global Constraints

- psycopg 3 async only; no asyncpg. SQL lives under `db/sql/` as parameterized files loaded through `csa.core.db.q`.
- Only `csa/graph/` may import LangGraph; nothing in this plan touches `csa/graph/`.
- Migrations are additive and ordered; the new file is `db/migrations/0450_console_service_packages.sql`; RLS enabled, never `FORCE`.
- `EXPECTED_SCHEMA_VERSION` becomes `"0450"` in `apps/gateway/main.py`, `tests/test_migrate.py` and `tests/test_hr_persistence.py` (line 1412 assertion).
- Required safety capabilities `escalation_agent` and `order_approval` are never set `is_enabled = false` by any path.
- Error string for an inactive package gate is exactly `package_inactive` with HTTP 403.
- Audit actions: `service.requested`, `service.activated`, `service.suspended`; details JSON `{"before": <status|null>, "after": <status>, "reference": <str|null>}`.
- No secrets, production connections or live providers in tests: `NO_LOCAL_ENV=1 ENV=ci`, disposable Testcontainers Postgres.
- Frontend files added under `lib/`, `components/` or `tests/` that are not inside `app/` or `components/console/` must be added to the file list in `CSA_WebConsole/csa-console/web/scripts/check-isolated.mjs`.
- The spec's `GET /console/v1/me` extension is implemented as `GET /console/v1/services/active` on the console router instead, because `/auth/me` is served by the identity router from the identity pool, which has no grant on the new table. Behaviour is otherwise as specified.
- Done means `NO_LOCAL_ENV=1 ENV=ci make lint test`, the isolated frontend check, and `tests/test_migrate.py::test_make_reset_only_disposable_container` pass on the final source.

## Review Focus

1. Activating an add-on whose base package is only `requested` must return 409, not activate. Pinned in Task 4.
2. Suspending `customer_support` while an add-on is `active` must return 409; suspending an add-on must leave `kb_agent`, `escalation_agent` and `order_approval` untouched. Pinned in Task 3.
3. A tenant admin switching one member agent off after activation must not change package status, and `GET /services` must report it as an inconsistency. Pinned in Task 3.
4. A read-only member of a tenant without `customer_support` must get 403 `package_inactive` from `GET /console/v1/kb/documents`, not an empty list. Pinned in Task 5.
5. A platform admin with no tenant selected must still see every navigation item, and a tenant member must see only package-active items. Pinned in Task 10.

---

### Task 1: Migration 0450 and schema version bump

**Files:**
- Create: `db/migrations/0450_console_service_packages.sql`
- Modify: `apps/gateway/main.py:56`, `tests/test_migrate.py:19`, `tests/test_hr_persistence.py:1412`
- Test: `tests/test_migrate.py` (existing), `tests/test_console_services.py` (new, backfill test only in this task)

**Interfaces:**
- Produces: table `public.console_service_packages(client_id uuid, package text, status text, requested_by uuid, requested_at timestamptz, activated_by uuid, activated_at timestamptz, reference text, note text, updated_at timestamptz)` with `PRIMARY KEY (client_id, package)`, `status IN ('requested','active','suspended')`, tenant RLS policy `tenant_isolation` on `client_id = nullif(current_setting('app.client_id',true),'')::uuid`, grants `SELECT, INSERT, UPDATE` to `csa_app`, nothing to `csa_ro`/`csa_identity`.

- [ ] **Step 1: Write the failing backfill test**

Create `tests/test_console_services.py` with `pytest_plugins = ["tests.test_console_sessions"]` and:

```python
async def test_backfill_marks_fully_enabled_packages_active(sessions: SessionWorld) -> None:
    tenant = sessions.tenants[0]
    for name in ("kb_agent", "ticket_agent", "escalation_agent", "order_approval"):
        await sessions.q("UPDATE capability_registry SET is_enabled=true WHERE client_id=%s AND name=%s", tenant, name)
    await sessions.q("UPDATE capability_registry SET is_enabled=true WHERE client_id=%s AND name='order_agent'", tenant)
    await sessions.q("DELETE FROM console_service_packages WHERE client_id=%s", tenant)
    await sessions.q(open("db/migrations/0450_console_service_packages.sql").read().split("-- BACKFILL")[1])
    rows = await sessions.q("SELECT package, status, reference FROM console_service_packages WHERE client_id=%s ORDER BY package", tenant)
    assert rows == [{"package": "customer_support", "status": "active", "reference": "backfill-0450"}]
```

The migration marks its backfill statement with a `-- BACKFILL` comment line so the test can re-run exactly that statement; `orders_products` is not backfilled because `invoice_agent` stays disabled; `staff_email` and `hr` are not backfilled because the tenant has `email_settings.enabled = false` and no enabled `hr_agent_settings` row.

- [ ] **Step 2: Run the test to verify it fails**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_console_services.py::test_backfill_marks_fully_enabled_packages_active`
Expected: FAIL (table does not exist).

- [ ] **Step 3: Write the migration**

`db/migrations/0450_console_service_packages.sql`: header comment with preconditions (`0440` applied) and rollback (drop table; no data elsewhere changes); `CREATE TABLE` as in Interfaces, with `CHECK (length(reference) <= 200)`, `CHECK (length(note) <= 1000)`, `CHECK (status <> 'active' OR activated_at IS NOT NULL)`; RLS, grants; then a line `-- BACKFILL` followed by one `INSERT ... SELECT` that, per client, inserts `('customer_support','active', NULL, NULL, NULL, now(), 'backfill-0450', 'Backfilled from fully enabled capability switches')` where all of `kb_agent, ticket_agent, escalation_agent, order_approval` have `is_enabled`; `orders_products` where `order_agent` and `invoice_agent` do; `subscriptions`/`bookings`/`marketplaces`/`e_invoicing` for their single members; `hr` where any `hr_agent_settings` row for the client is enabled; `staff_email` where `email_settings.enabled`. `ON CONFLICT DO NOTHING`. Package member lists are duplicated here on purpose; Task 2's consistency test compares them with the catalogue.

- [ ] **Step 4: Bump the expected schema version in the three files and run migration tests**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_migrate.py tests/test_console_services.py::test_backfill_marks_fully_enabled_packages_active`
Expected: all PASS, including `test_make_reset_only_disposable_container`.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0450_console_service_packages.sql apps/gateway/main.py tests/test_migrate.py tests/test_hr_persistence.py tests/test_console_services.py
git commit -m "feat(console): add service package records migration 0450"
```

---

### Task 2: Package catalogue module

**Files:**
- Create: `csa/console/packages.py`
- Test: `tests/test_console_packages.py`

**Interfaces:**
- Produces:
  ```python
  Kind = Literal["base", "addon", "standalone"]
  class Package(BaseModel):  # frozen, extra="forbid"
      key: str; title: str; summary: str; kind: Kind
      requires: tuple[str, ...]; capabilities: tuple[str, ...]
      hr_modules: tuple[str, ...]; email: bool; tabs: tuple[str, ...]; sellable: bool
  PACKAGES: tuple[Package, ...]          # ordered as in the spec table
  BY_KEY: dict[str, Package]
  def package(key: str) -> Package       # raises KeyError
  def packages_for_capability(name: str) -> tuple[Package, ...]
  def backfill_members() -> dict[str, tuple[str, ...]]  # key -> capabilities, used by Task 1 consistency test
  ```
- Tab keys: `inbox`, `knowledge_base`, `business`, `subscriptions`, `bookings`, `ecommerce`, `lhdn_myinvois`, `hr`, `email`.

- [ ] **Step 1: Write the failing tests**

```python
def test_every_customer_agent_belongs_to_exactly_one_package():
    counts = Counter(c for p in PACKAGES for c in p.capabilities)
    assert set(counts) == set(CATALOG) - {"email_assistant"}
    assert all(n == 1 for n in counts.values())

def test_required_safety_capabilities_live_only_in_customer_support():
    assert packages_for_capability("escalation_agent") == (package("customer_support"),)
    assert packages_for_capability("order_approval") == (package("customer_support"),)

def test_addons_require_customer_support_and_standalones_require_nothing():
    for p in PACKAGES:
        assert (p.kind == "addon") == (p.requires == ("customer_support",))
        if p.kind != "addon": assert p.requires == ()

def test_sellable_is_false_when_any_member_is_not_implemented():
    for p in PACKAGES:
        if any(CATALOG[c][1] == "not_implemented" for c in p.capabilities): assert p.sellable is False
    assert package("marketplaces").sellable is False
    assert package("e_invoicing").sellable is False
    assert package("customer_support").sellable and package("hr").sellable and package("staff_email").sellable

def test_migration_backfill_members_match_catalogue():
    sql = Path("db/migrations/0450_console_service_packages.sql").read_text()
    for key, members in backfill_members().items():
        for member in members: assert f"'{member}'" in sql.split("-- BACKFILL")[1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_console_packages.py`
Expected: FAIL with import error.

- [ ] **Step 3: Implement `csa/console/packages.py`**

Nine entries per the spec table: `customer_support` (base; `kb_agent, ticket_agent, escalation_agent, order_approval`; tabs `inbox, knowledge_base`), `orders_products` (addon; `order_agent, invoice_agent`; tab `business`), `subscriptions` (addon; `subscription_agent`; tab `subscriptions`), `bookings` (addon; `booking_agent`; tab `bookings`), `marketplaces` (addon; `ecommerce_agent`; tab `ecommerce`; `sellable=False`), `e_invoicing` (addon; `lhdn_agent`; tab `lhdn_myinvois`; `sellable=False`), `hr` (standalone; `hr_modules=("hr_directory","hr_training","hr_recruitment","hr_attendance","hr_leave","hr_payroll","hr_assistant")` as listed in `db/migrations/0410_hr_operations.sql:271`; tab `hr`), `staff_email` (standalone; `email=True`; tab `email`). Summaries are one sentence each, written for a tenant admin.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_console_packages.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add csa/console/packages.py tests/test_console_packages.py
git commit -m "feat(console): define the service package catalogue"
```

---

### Task 3: Service layer: list, request, activate, suspend

**Files:**
- Create: `csa/console/services.py`, `db/sql/console/service_package_get.sql`, `db/sql/console/service_packages_list.sql`, `db/sql/console/service_package_request.sql`, `db/sql/console/service_package_activate.sql`, `db/sql/console/service_package_suspend.sql`
- Test: `tests/test_console_services.py`

**Interfaces:**
- Consumes: `csa.console.packages`; `db.q.fetch_one("console/capability_lock.sql", (client_id, name))`; `db.q.fetch_one("capability_registry/update_enabled.sql", (is_enabled, client_enabled, client_enabled, actor_id_str, client_id, name))`; `db.q.execute("console/audit.sql", (actor_id, client_id, action, target, Jsonb(details)))`; `db.q.fetch_one("email/settings_get.sql", ...)` for the email inner switch (read its parameters from the file).
- Produces:
  ```python
  Status = Literal["available", "requested", "active", "suspended"]
  class PackageState(BaseModel): key: str; status: Status
  class ServiceOut(BaseModel):  # extra="forbid"
      key, title, summary, kind, requires: list[str], sellable: bool, tabs: list[str]
      status: Status; reference: str | None; note: str | None
      requested_at, activated_at: datetime | None
      requested_by, activated_by: str | None   # display names
      agents: list[AgentOut]; inner_switch: bool | None   # staff_email only
      consistent: bool; inconsistencies: list[str]
      can_request: bool; can_activate: bool; can_suspend: bool
  class ActivateIn(BaseModel): reference: str = Field(min_length=1, max_length=200); note: str | None = Field(default=None, max_length=1000)
  class SuspendIn(BaseModel): note: str | None = Field(default=None, max_length=1000)
  async def list_services(pool, client_id: UUID, *, actor: ConsoleActor | None, kb_model: str | None, kb_tei_configured: bool) -> list[ServiceOut]
  async def active_packages(pool, client_id: UUID) -> list[PackageState]   # only status == "active"
  async def request_package(pool, client_id: UUID, key: str, *, actor: ConsoleActor) -> ServiceOut
  async def activate_package(pool, client_id: UUID, key: str, body: ActivateIn, *, actor: ConsoleActor) -> ServiceOut
  async def suspend_package(pool, client_id: UUID, key: str, body: SuspendIn, *, actor: ConsoleActor) -> ServiceOut
  class PackageError(HTTPException)  # 409 for state conflicts, 422 for unknown/unsellable key
  ```
- Permission flags: `can_request` for `tenant_admin` or `platform_admin` when status in (`available`, `suspended`) and `sellable`; `can_activate` for `platform_admin` when status != `active` and `sellable` and all `requires` active; `can_suspend` for `platform_admin` when `active`. Machine principals and `None` actors get all false.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_console_services.py`; these call the service functions directly with `sessions.pool`)

```python
async def test_request_then_activate_flips_member_switches_and_audits(sessions):
    admin = ConsoleActor.model_validate({**(await sessions.admin.get("/console/v1/auth/me")).json()["user"], ...})  # build via helper `actor_for(sessions, client)` defined in the test module from /auth/me + session cookie
    out = await request_package(sessions.pool, tenant, "customer_support", actor=tenant_admin_actor)
    assert out.status == "requested" and out.requested_by == "Synthetic Member"
    out = await activate_package(sessions.pool, tenant, "customer_support", ActivateIn(reference="Q-2026-001"), actor=admin)
    assert out.status == "active" and out.reference == "Q-2026-001" and out.consistent
    caps = {r["name"]: r for r in await sessions.q("SELECT name,is_enabled,client_enabled FROM capability_registry WHERE client_id=%s", tenant)}
    assert all(caps[n]["is_enabled"] and caps[n]["client_enabled"] for n in ("kb_agent","ticket_agent","escalation_agent","order_approval"))
    assert not caps["order_agent"]["is_enabled"]
    audit = await sessions.q("SELECT action, target_id, details FROM console_audit WHERE client_id=%s AND action LIKE 'service.%%' ORDER BY created_at", tenant)
    assert [a["action"] for a in audit] == ["service.requested", "service.activated"]
    assert audit[1]["details"] == {"before": "requested", "after": "active", "reference": "Q-2026-001"}
    assert 4 == len(await sessions.q("SELECT 1 FROM console_audit WHERE client_id=%s AND action='capability.updated'", tenant))

async def test_addon_requires_active_base_and_base_cannot_suspend_under_active_addon(sessions):
    with pytest.raises(PackageError) as e: await activate_package(pool, tenant, "orders_products", ActivateIn(reference="Q"), actor=admin)
    assert e.value.status_code == 409
    await activate_package(pool, tenant, "customer_support", ActivateIn(reference="Q"), actor=admin)
    await activate_package(pool, tenant, "orders_products", ActivateIn(reference="Q"), actor=admin)
    with pytest.raises(PackageError) as e: await suspend_package(pool, tenant, "customer_support", SuspendIn(), actor=admin)
    assert e.value.status_code == 409
    await suspend_package(pool, tenant, "orders_products", SuspendIn(note="trial ended"), actor=admin)
    caps = ...
    assert not caps["order_agent"]["is_enabled"] and not caps["invoice_agent"]["is_enabled"]
    assert caps["kb_agent"]["is_enabled"] and caps["escalation_agent"]["is_enabled"] and caps["order_approval"]["is_enabled"]

async def test_manual_switch_off_keeps_package_active_but_reports_drift(sessions):
    ... activate customer_support ...
    await sessions.q("UPDATE capability_registry SET is_enabled=false WHERE client_id=%s AND name='ticket_agent'", tenant)
    svc = {s.key: s for s in await list_services(pool, tenant, actor=admin, kb_model=None, kb_tei_configured=False)}
    assert svc["customer_support"].status == "active" and svc["customer_support"].consistent is False
    assert svc["customer_support"].inconsistencies == ["ticket_agent"]

async def test_unknown_and_unsellable_keys_and_required_switch_protection(sessions):
    for key in ("nope", "marketplaces"):
        with pytest.raises(PackageError) as e: await request_package(pool, tenant, key, actor=tenant_admin_actor)
        assert e.value.status_code == 422
    await activate_package(pool, tenant, "customer_support", ActivateIn(reference="Q"), actor=admin)
    await suspend_package(pool, tenant, "customer_support", SuspendIn(), actor=admin)
    caps = ...
    assert caps["escalation_agent"]["is_enabled"] and caps["order_approval"]["is_enabled"]
    assert not caps["kb_agent"]["is_enabled"] and not caps["ticket_agent"]["is_enabled"]

async def test_staff_email_and_hr_have_no_capability_members_and_email_inner_switch_is_reported(sessions):
    svc = {s.key: s for s in await list_services(...)}
    assert svc["staff_email"].agents == [] and svc["staff_email"].inner_switch is False
    assert svc["hr"].inner_switch is None
    assert svc["customer_support"].inner_switch is None
```

The module defines a helper `async def actors(sessions) -> tuple[ConsoleActor, ConsoleActor]` that logs in a tenant admin via `sessions.member(role="tenant_admin")` and builds `ConsoleActor` objects through `sessions.service.actor_for_request`-equivalent data from `/auth/me` responses (fields: `user_id`, `email`, `display_name`, `platform_admin`, `must_change_password`, `session_id`, `client_id`, `role`, `memberships`); read `csa/console/contracts.py:45` for the exact field list.

- [ ] **Step 2: Run tests to verify they fail**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_console_services.py`
Expected: FAIL with import error for `csa.console.services`.

- [ ] **Step 3: Write the SQL files**

- `service_package_get.sql`: `SELECT * FROM public.console_service_packages WHERE client_id=%s AND package=%s FOR UPDATE`.
- `service_packages_list.sql`: `SELECT p.*, r.display_name AS requested_by_name, a.display_name AS activated_by_name FROM public.console_service_packages p LEFT JOIN public.console_users r ON r.id=p.requested_by LEFT JOIN public.console_users a ON a.id=p.activated_by WHERE p.client_id=%s`. If `console_users` is not readable by `csa_app` under the tenant context, return the UUIDs and resolve display names through `console/identity` SQL already used by team pages; check `db/sql/console/identity/`.
- `service_package_request.sql`: `INSERT ... (client_id, package, status, requested_by, requested_at) VALUES (%s,%s,'requested',%s,now()) ON CONFLICT (client_id, package) DO UPDATE SET status='requested', requested_by=EXCLUDED.requested_by, requested_at=now(), updated_at=now() WHERE console_service_packages.status <> 'active' RETURNING *`.
- `service_package_activate.sql`: upsert to `active` with `activated_by, activated_at=now(), reference, note` RETURNING *.
- `service_package_suspend.sql`: `UPDATE ... SET status='suspended', note=COALESCE(%s, note), updated_at=now() WHERE client_id=%s AND package=%s RETURNING *`.

- [ ] **Step 4: Implement `csa/console/services.py`**

Inside one `tx(pool, str(client_id))` per mutation: take `pg_advisory_xact_lock(hashtextextended('service:'||client_id||':'||key,0))` via a one-line SQL file `console/service_package_lock.sql`; validate key and `sellable` (422); load current row; check `requires` all active (409); for activate, for each member capability run `capability_lock.sql` then `update_enabled.sql` with `(True, True, True, str(actor.user_id), client_id, name)` and write the `capability.updated` audit exactly as `toggle_capability` does; for suspend, compute `shared = {c for other in PACKAGES if other.key != key and other active for c in other.capabilities}` and set `is_enabled=False` only for members not in `shared` and not in `REQUIRED`; refuse suspend when any active package lists `key` in `requires` (409). `inconsistencies` = members whose `is_enabled` differs from `status == "active"`. `inner_switch` reads `email/settings_get.sql` for `staff_email`, else `None`. `agents` reuses `agents.list_agents(...)` filtered to member names.

- [ ] **Step 5: Run tests to verify they pass**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_console_services.py tests/test_console_packages.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add csa/console/services.py db/sql/console/service_package_*.sql db/sql/console/service_packages_list.sql tests/test_console_services.py
git commit -m "feat(console): service package request, activation and suspension"
```

---

### Task 4: Services API routes and contract snapshot

**Files:**
- Modify: `csa/console/services.py` (add `build_services_router(kb_model, kb_tei_configured) -> APIRouter`), `csa/console/router.py:142-148` (include it; pass the two KB arguments `build_router` already receives), `docs/console_openapi.json` (regenerate)
- Test: `tests/test_console_services.py`

**Interfaces:**
- Produces routes under `/console/v1/services`:
  - `GET /services` → `list[ServiceOut]` (any human session; machine principals 403 `human session required`)
  - `GET /services/active` → `{"packages": list[PackageState]}`; for a platform admin without `client_id` returns `{"packages": []}` instead of 400 (custom dependency: if `actor.platform_admin` and no `client_id` query, skip `tenant`)
  - `POST /services/{key}/request` → `ServiceOut`; roles `tenant_admin`, `platform_admin`; else 403 `tenant admin required`
  - `POST /services/{key}/activate` body `ActivateIn` → `ServiceOut`; `platform_admin` only, else 403 `platform admin required`
  - `POST /services/{key}/suspend` body `SuspendIn` → `ServiceOut`; `platform_admin` only
- All mutations require CSRF through the existing `authenticate` dependency behaviour for cookie sessions (same as `PATCH /capabilities/{name}`).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("role", ["tenant_admin", "tenant_staff", "read_only"])
async def test_services_routes_permissions(sessions, role):
    browser, _ = await sessions.member(role=role)
    assert (await browser.get("/console/v1/services")).status_code == 200
    req = await sessions.mutate(browser, "/console/v1/services/customer_support/request")
    assert req.status_code == (200 if role == "tenant_admin" else 403)
    act = await sessions.mutate(browser, "/console/v1/services/customer_support/activate", {"reference": "Q"})
    assert act.status_code == 403
    assert (await browser.get("/console/v1/services", params={"client_id": sessions.tenants[1]})).status_code == 403

async def test_platform_admin_activates_requested_addon_only_after_base(sessions):
    t = sessions.tenants[0]
    assert (await sessions.mutate(sessions.admin, f"/console/v1/services/orders_products/request?client_id={t}")).status_code == 200
    assert (await sessions.mutate(sessions.admin, f"/console/v1/services/orders_products/activate?client_id={t}", {"reference": "Q"})).status_code == 409
    assert (await sessions.mutate(sessions.admin, f"/console/v1/services/customer_support/activate?client_id={t}", {"reference": "Q-1"})).status_code == 200
    assert (await sessions.mutate(sessions.admin, f"/console/v1/services/orders_products/activate?client_id={t}", {"reference": "Q-2"})).status_code == 200
    active = (await sessions.admin.get(f"/console/v1/services/active?client_id={t}")).json()
    assert sorted(p["key"] for p in active["packages"]) == ["customer_support", "orders_products"]
    assert (await sessions.admin.get("/console/v1/services/active")).json() == {"packages": []}

async def test_machine_token_cannot_read_or_change_services(sessions):
    headers = {"Authorization": f"Bearer {MACHINE_TOKEN}", "X-Client-Id": sessions.tenants[0]}
    assert (await sessions.admin.get("/console/v1/services", headers=headers, cookies={})).status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_console_services.py -k "routes or platform_admin_activates or machine"`
Expected: FAIL with 404s.

- [ ] **Step 3: Implement `build_services_router` and include it in `build_router`**

- [ ] **Step 4: Regenerate the OpenAPI snapshot and run tests**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run python -m scripts.export_console_openapi && NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_console_services.py tests/test_console_contract_snapshot.py`
Expected: PASS; `git diff --stat docs/console_openapi.json` shows only added `/console/v1/services*` paths and schemas.

- [ ] **Step 5: Commit**

```bash
git add csa/console/services.py csa/console/router.py docs/console_openapi.json tests/test_console_services.py
git commit -m "feat(console): services API for packages"
```

---

### Task 5: Package gates for Inbox, Business and Knowledge base

**Files:**
- Modify: `csa/console/services.py` (add gate), `csa/console/inbox.py:126-134`, `csa/console/business.py:112-116` and `:160-168`, `csa/console/router.py` KB routes at lines 292-387 and 388-430 (invoices)
- Test: `tests/test_console_services.py`

**Interfaces:**
- Produces:
  ```python
  async def require_package(pool: AsyncConnectionPool, client_id: UUID, keys: frozenset[str]) -> None
      # 403 HTTPException(detail="package_inactive") unless at least one key is active
  def package_gate(*keys: str)  # FastAPI dependency factory using Depends(tenant) and request.app.state.pool
  ```
- Gate assignments: Inbox routes → `package_gate("customer_support")`; Business routes → `package_gate("orders_products", "marketplaces")`; KB routes (`/kb/*`) and `/invoices*` → `package_gate("customer_support")` for KB, `package_gate("orders_products")` for invoices; `/tabs/specialized`, `/agents`, `/capabilities`, `/overview`, `/runs`, `/traces`, `/events` unchanged.

- [ ] **Step 1: Write the failing tests**

```python
async def test_inactive_packages_block_inbox_business_kb_for_every_role(sessions):
    for role in ("tenant_admin", "tenant_staff", "read_only"):
        browser, _ = await sessions.member(role=role)
        for path in ("/console/v1/kb/documents", "/console/v1/inbox/conversations", "/console/v1/business/orders"):
            r = await browser.get(path)
            assert r.status_code == 403 and r.json()["detail"] == "package_inactive", (role, path, r.text)
    t = sessions.tenants[0]
    await sessions.mutate(sessions.admin, f"/console/v1/services/customer_support/activate?client_id={t}", {"reference": "Q"})
    reader, _ = await sessions.member(role="read_only")
    assert (await reader.get("/console/v1/kb/documents")).status_code == 200
    assert (await reader.get("/console/v1/business/orders")).status_code == 403
    staff, _ = await sessions.member(role="tenant_staff")
    assert (await staff.get("/console/v1/inbox/conversations")).status_code == 200
```

Confirm the exact inbox and business list paths from `build_inbox_router` and `build_business_router` before writing the assertions.

- [ ] **Step 2: Run the test to verify it fails**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_console_services.py::test_inactive_packages_block_inbox_business_kb_for_every_role`
Expected: FAIL (200 or empty lists where 403 expected).

- [ ] **Step 3: Implement `require_package`, `package_gate`, and attach dependencies**

`require_package` runs `SELECT 1 FROM public.console_service_packages WHERE client_id=%s AND status='active' AND package = ANY(%s) LIMIT 1` from a new `db/sql/console/service_package_any_active.sql` under `tx`. Attach with `dependencies=[Depends(package_gate(...))]` on the inbox and business `APIRouter` constructors and on each KB and invoice route decorator. Keep the role checks that exist.

- [ ] **Step 4: Run the full console test selection**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_console_services.py tests/test_console_sessions.py tests/test_console_inbox.py tests/test_console_business.py tests/test_console_kb_uploads.py tests/test_console_capabilities.py`
Expected: PASS. Existing inbox, business and KB tests that run against disposable tenants will now need `customer_support`/`orders_products` activated in their fixtures; add one helper `activate(sessions, tenant, *keys)` in `tests/test_console_services.py` and import it where needed rather than toggling capabilities directly.

- [ ] **Step 5: Commit**

```bash
git add csa/console/services.py csa/console/inbox.py csa/console/business.py csa/console/router.py db/sql/console/service_package_any_active.sql tests/
git commit -m "feat(console): gate inbox, business and knowledge base by active package"
```

---

### Task 6: Package gates for HR and Email APIs

**Files:**
- Modify: `csa/hr/api.py:116-160` (`_session`), `csa/email/api.py:113-150` (`_resolve`)
- Test: `tests/test_console_services.py`

**Interfaces:**
- Consumes: `require_package(request.app.state.pool, tenant_id, frozenset({"hr"}))` and `frozenset({"staff_email"})`, called after the tenant is resolved and before CSRF.

- [ ] **Step 1: Write the failing test**

Extend the test app in `tests/test_console_services.py` with a fixture that includes `build_hr_router()` and `build_email_router()` on `sessions.app` (both need `app.state.identity_service`, already present in `SessionWorld`; the email router also needs `app.state.email_service`; set a minimal stub only if `_resolve` touches it before the gate, otherwise leave unset).

```python
async def test_hr_and_email_apis_require_their_packages(sessions, services_app):
    browser, _ = await sessions.member(role="tenant_admin")
    assert (await browser.get("/console/v1/hr/context")).status_code == 403
    assert (await browser.get("/console/v1/email/context")).status_code == 403
    t = sessions.tenants[0]
    await sessions.mutate(sessions.admin, f"/console/v1/services/hr/activate?client_id={t}", {"reference": "HR-1"})
    assert (await browser.get("/console/v1/hr/context")).status_code != 403
    assert (await browser.get("/console/v1/email/context")).json()["detail"] == "package_inactive"
```

Read the first GET route each router exposes and use those paths.

- [ ] **Step 2: Run to verify it fails**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_console_services.py::test_hr_and_email_apis_require_their_packages`
Expected: FAIL.

- [ ] **Step 3: Insert the gate calls and run HR and email suites**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_console_services.py tests/test_hr_persistence.py tests/test_hr_operations.py tests/test_hr_workflows.py tests/test_hr_training.py tests/test_email_service.py tests/test_email_contracts.py`
Expected: PASS. `HrWorld` fixtures (`tests/test_hr_persistence.py:334`) must activate the `hr` package for their tenant; insert the row directly with `world.q` in the fixture.

- [ ] **Step 4: Commit**

```bash
git add csa/hr/api.py csa/email/api.py tests/
git commit -m "feat(console): require hr and staff_email packages on their APIs"
```

---

### Task 7: Frontend transport module for services

**Files:**
- Create: `CSA_WebConsole/csa-console/web/lib/services-api.ts`, `CSA_WebConsole/csa-console/web/tests/services-api.test.mjs`
- Modify: `CSA_WebConsole/csa-console/web/scripts/check-isolated.mjs` (add both files to the list; keep the two sections in sync as the voice/email entries are)

**Interfaces:**
- Produces:
  ```ts
  export type PackageStatus = "available" | "requested" | "active" | "suspended";
  export interface PackageState { key: string; status: PackageStatus }
  export interface Service { key: string; title: string; summary: string; kind: "base" | "addon" | "standalone"; requires: string[]; sellable: boolean; tabs: string[]; status: PackageStatus; reference: string | null; note: string | null; requested_at: string | null; activated_at: string | null; requested_by: string | null; activated_by: string | null; agents: AgentRow[]; inner_switch: boolean | null; consistent: boolean; inconsistencies: string[]; can_request: boolean; can_activate: boolean; can_suspend: boolean }
  export function servicesClient(clientId: string | null): { list(): Promise<Service[]>; active(): Promise<PackageState[]>; request(key): Promise<Service>; activate(key, reference, note?): Promise<Service>; suspend(key, note?): Promise<Service> }
  ```
  `clientId` null means no `?client_id=` query (tenant member); a string appends it (platform admin). `AgentRow` is the existing agent row type used by `RuntimeWorkspace.tsx`; import or re-declare its fields.

- [ ] **Step 1: Write the failing transport test** modelled on `tests/email-transport.test.mjs`: assert every path starts with `/console/v1/services`, mutations carry `X-CSRF-Token`, `credentials: "same-origin"`, `cache: "no-store"`, no identity headers, `activate` sends `{reference, note}` JSON, tenant query appended only when `clientId` is given.

- [ ] **Step 2: Run to verify it fails**

Run: `cd CSA_WebConsole/csa-console/web && node --test tests/services-api.test.mjs`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `lib/services-api.ts` and register both files in `check-isolated.mjs`**

- [ ] **Step 4: Run the frontend test and the isolated check**

Run: `cd CSA_WebConsole/csa-console/web && node --test tests/services-api.test.mjs && cd - && PATH=/tmp/fourstacks-toolbin:$PATH npm --prefix CSA_WebConsole/csa-console/web run check:isolated`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add CSA_WebConsole/csa-console/web/lib/services-api.ts CSA_WebConsole/csa-console/web/tests/services-api.test.mjs CSA_WebConsole/csa-console/web/scripts/check-isolated.mjs
git commit -m "feat(console-web): services transport client"
```

---

### Task 8: Navigation from active packages

**Files:**
- Modify: `CSA_WebConsole/csa-console/web/lib/server-identity.ts` (add `export async function activePackages(): Promise<PackageState[]>` fetching `/console/v1/services/active` with the same cookie forwarding `requireIdentity` uses; on 403 return `[]`), `app/(admin)/layout.tsx`, `app/portal/layout.tsx` (fetch and pass `packages`), `components/AppShell.tsx:33-60` (new prop `packages: PackageState[]`), `app/(admin)/agents/page.tsx` and `app/portal/agents/page.tsx` (redirect to `/services` and `/portal/services`)
- Test: `CSA_WebConsole/csa-console/web/tests/nav-visibility.test.mjs` (new; add to `check-isolated.mjs`)

**Interfaces:**
- Produces `export function navigationFor(me: Me, packages: PackageState[]): NavLink[]` exported from a new `lib/navigation.ts` (add to `check-isolated.mjs`) so it is testable without React; `AppShell` calls it.
- Rules: tab → package: `inbox`→`customer_support`, `knowledge_base`→`customer_support`, `business`→`orders_products|marketplaces`, `hr`→`hr`, `email`→`staff_email`. A link renders when `me.role === "platform_admin"` (all links, regardless of `packages`) or when its package is active and the existing role rule allows. "Agents" becomes "Services" at `/services` or `/portal/services`. Overview, Runtime activity, Sandbox assistant, Staff workspace, Tenants/Members/Team and Password keep today's rules.

- [ ] **Step 1: Write the failing test**

```js
test("tenant member sees only package-active tabs; platform admin sees all", () => {
  const staff = { role: "tenant_staff", user: {...}, memberships: [], active_client_id: "t", csrf_token: "" };
  const none = navigationFor(staff, []).map(l => l.label);
  assert.deepEqual(none.filter(l => ["Customer inbox","Knowledge base","Business management","People & HR","Email assistant"].includes(l)), []);
  const some = navigationFor(staff, [{key:"customer_support",status:"active"},{key:"hr",status:"active"}]).map(l => l.label);
  assert.ok(some.includes("Customer inbox") && some.includes("Knowledge base") && some.includes("People & HR"));
  assert.ok(!some.includes("Business management") && !some.includes("Email assistant"));
  assert.ok(some.includes("Services") && !some.includes("Agents"));
  const admin = navigationFor({ ...staff, role: "platform_admin" }, []).map(l => l.label);
  for (const l of ["Customer inbox","Knowledge base","Business management","People & HR","Email assistant","Services"]) assert.ok(admin.includes(l), l);
  const reader = navigationFor({ ...staff, role: "read_only" }, [{key:"customer_support",status:"active"},{key:"staff_email",status:"active"}]).map(l => l.label);
  assert.ok(reader.includes("Knowledge base") && !reader.includes("Customer inbox") && !reader.includes("Email assistant"));
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd CSA_WebConsole/csa-console/web && node --test tests/nav-visibility.test.mjs`
Expected: FAIL.

- [ ] **Step 3: Implement `lib/navigation.ts`, wire `AppShell`, layouts, `activePackages`, and the two redirects**

- [ ] **Step 4: Run frontend checks**

Run: `PATH=/tmp/fourstacks-toolbin:$PATH npm --prefix CSA_WebConsole/csa-console/web run check:isolated`
Expected: PASS (tests, lint, types, build).

- [ ] **Step 5: Commit**

```bash
git add CSA_WebConsole/csa-console/web
git commit -m "feat(console-web): build navigation from active service packages"
```

---

### Task 9: Services tab

**Files:**
- Create: `CSA_WebConsole/csa-console/web/components/console/ServicesWorkspace.tsx`, `app/(admin)/services/page.tsx`, `app/portal/services/page.tsx`
- Modify: `components/console/RuntimeWorkspace.tsx` only if the agent row component must be exported for reuse

**Interfaces:**
- Consumes: `servicesClient` from Task 7; `me` from `requireIdentity()`; for platform admins, the tenant selector pattern used by `RuntimeWorkspace.tsx:33` (`?client_id=` query or local state).
- Produces: a page with three groups (Base, Add-ons, Standalone); cards showing title, summary, status badge (`Available`, `Requested`, `Active`, `Suspended`, `Coming soon` when `!sellable`), reference and `activated_at`, expandable member agents (reuse the existing agent row and per-agent switch controls), `Request` button when `can_request`, `Activate` opening an inline confirmation with a required reference input and optional note when `can_activate`, `Suspend` with confirmation when `can_suspend`, an "Enabled by your tenant admin in the Email tab" hint for `staff_email` using `inner_switch`, static "HR modules are enabled by your HR owner inside the HR tab" for `hr`, and a drift warning listing `inconsistencies` for platform admins. Every mutation disables its button while in flight and shows the `ApiError.message` on failure; a 409 shows the server message verbatim.

- [ ] **Step 1: Implement the component and pages** (no unit test; the browser acceptance in Task 10 covers behaviour)

- [ ] **Step 2: Run frontend checks**

Run: `PATH=/tmp/fourstacks-toolbin:$PATH npm --prefix CSA_WebConsole/csa-console/web run check:isolated`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add CSA_WebConsole/csa-console/web
git commit -m "feat(console-web): services tab"
```

---

### Task 10: Browser acceptance for navigation and the Services tab

**Files:**
- Create: `tests/test_services_browser.py`

**Interfaces:**
- Consumes the staging pattern in `tests/test_voice_browser.py:25-70`: `check-isolated.mjs --context`, symlinked `node_modules`, a replaced `login/page.tsx` harness, `next dev --webpack` on a free port, Playwright routes answering `/console/v1/*` with synthetic JSON. The harness page renders `AppShell` with a synthetic `me` and the `ServicesWorkspace`.

- [ ] **Step 1: Write the test**

`test_navigation_and_services_tab_follow_package_state`: route `/console/v1/services/active` → `{"packages": []}` and assert the sidebar has no Inbox/KB/Business/HR/Email links for a `tenant_admin` `me`; re-route with `customer_support` active and reload; assert Inbox and Knowledge base appear; open `/services`, click `Request` on Orders and Products and assert the POST hit `/console/v1/services/orders_products/request` with CSRF; switch `me.role` to `platform_admin` with `/console/v1/services/active` returning `[]` and assert all five links are present; click `Activate` on Customer Support, fill reference `Q-1`, confirm, and assert the POST body `{"reference":"Q-1","note":null}`; navigate directly to `/inbox` with the API returning 403 `package_inactive` and assert the page shows that message and no conversation list. Mark the module `pytestmark = [requires_docker_free_node, pytest.mark.asyncio]` following the voice browser test's marker, and add the same `node_modules/next` precondition assertion.

- [ ] **Step 2: Run the test**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_services_browser.py`
Expected: PASS (1 test). On failure, Playwright trace and frontend log are written under `tmp_path` as in the voice browser test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_services_browser.py
git commit -m "test(console): browser acceptance for package navigation and services tab"
```

---

### Task 11: Documentation for the package layer

**Files:**
- Modify: `docs/CONSOLE_API.md` (new `## Services and packages` section placed before `## Capabilities and specialised tabs (LG13)` at line 567; one sentence added to that section saying packages are the management surface and switches remain enforcement), `docs/PROJECT_OVERVIEW.md:90` (schema `0450`; replace the flat agent description with the package map table from the spec), `docs/ARCHITECTURE.md` (new paragraph under `## Visibility (console)` at line 83), `deploy/STAGING_PIPELINE.md:156-157` (remove the `shadow` clause; replace with "the tenant is in `auto` mode after the 2026-10-23 activation; replies that the gate holds appear here for approval"), `docs/PROGRESS.md` (new top entry with the test evidence from Tasks 1 to 10)

- [ ] **Step 1: Write the sections** using the spec's catalogue table and the routes from Task 4 verbatim; state the gate rule (`package_inactive`), the backfill rule, and that HR/email inner switches are unchanged.

- [ ] **Step 2: Verify links and whitespace**

Run: `git diff --check && python3 - <<'PY'` (the link checker used earlier in this repository: resolve every relative markdown link target and `#anchor` in the touched files) `PY`
Expected: no output from `git diff --check`; "all local links resolve".

- [ ] **Step 3: Commit**

```bash
git add docs/CONSOLE_API.md docs/PROJECT_OVERVIEW.md docs/ARCHITECTURE.md deploy/STAGING_PIPELINE.md docs/PROGRESS.md
git commit -m "docs: describe service packages, gates and navigation"
```

---

### Task 12: Documentation cleanup approved on 2026-10-25

**Files:**
- Delete: `docs/information.md`, `docs/handoffs/2026-10-21-developer-a-workspace-prompt.md`, `docs/handoffs/2026-10-21-developer-b-hr-prompt.md`
- Move (git mv) into `docs/archive/`: every `docs/handoffs/*.md` dated before `2026-10-24`, `docs/evals/` (as `docs/archive/evals/`), `docs/hr/HR1_VALIDATION_HISTORY.md`, `docs/hr/HR2_VALIDATION_HISTORY.md`
- Create: `docs/archive/README.md` (three lines: what the folder holds, that entries are dated evidence not current truth, and that `PROJECT_OVERVIEW.md` is the entry point), `docs/archive/PROGRESS-2026-10-early.md` (entries of `docs/PROGRESS.md` dated before `2026-10-20`, moved verbatim, with the `# Progress` heading)
- Modify: every file whose links the moves break (`README.md:19`, `docs/PROJECT_OVERVIEW.md:14,73`, `docs/ARCHITECTURE.md:6`, `docs/RUNBOOK.md`, `docs/langgraph-rebuild/LG01_foundation.md`, `docs/langgraph-rebuild/LG06_jev_decision_layer.md`, and any handoff cross-links found by the checker); `information.md` links point to `PROJECT_OVERVIEW.md` or `ARCHITECTURE.md`, the `#26-current-limitations-and-release-conditions` anchor link points to `PROJECT_OVERVIEW.md#current-status-and-release-boundary`
- Keep: `docs/hr/CUSTOMER_PRESENTATION_OUTLINE.md`, all `docs/langgraph-rebuild/`, runbooks, `schema_notes.md`, `EMAIL_ASSISTANT.md`, `CONSOLE_VOICE.md`

- [ ] **Step 1: Perform the deletions and moves with `git rm` and `git mv`; write the two new files; trim `PROGRESS.md`**

- [ ] **Step 2: Fix links and verify**

Run the link checker over every tracked `*.md` file; run `git diff --check`.
Expected: "all local links resolve"; no whitespace errors; `git status` shows renames, not delete-plus-add, for the moved files.

- [ ] **Step 3: Run lint (docs-only change, but `make lint` also runs `git diff --check` style formatters)**

Run: `NO_LOCAL_ENV=1 ENV=ci make lint`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add -A docs README.md
git commit -m "docs: archive dated handoffs and evaluations, remove superseded architecture reference"
```

---

### Task 13: Final verification

- [ ] **Step 1: Full verification on the final source**

Run: `PATH=/tmp/fourstacks-toolbin:$PATH NO_LOCAL_ENV=1 ENV=ci make lint test && PATH=/tmp/fourstacks-toolbin:$PATH npm --prefix CSA_WebConsole/csa-console/web run check:isolated && NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_migrate.py::test_make_reset_only_disposable_container`
Expected: lint clean; `make test` summary `N passed, 6 deselected` with zero failures; frontend check passes; reset test passes.

- [ ] **Step 2: Record results**

Append the exact pass counts and durations to the `docs/PROGRESS.md` entry from Task 11; commit as `docs(progress): record service package validation`.
