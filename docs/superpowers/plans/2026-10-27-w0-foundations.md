# W0 Shared Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the small shared changes both developer lanes depend on: a single schema-version source and reserved migration ranges, a reserved navigation key, the writing guide, the gateway router block, and an audited operator command that adds a platform administrator.

**Architecture:** Nothing here changes runtime behaviour for staff. The identity service gains one method (`add_platform_admin`) with a password-verified approver, exposed only through an operator script and a guarded compose one-shot, mirroring the existing bootstrap. Tests that hardcode the schema version import it from the gateway module so later merges touch one line.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 async, Pydantic, pytest + Testcontainers; Next.js/TypeScript with `node --test`; Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-27-client-growth-program-design.md` (section 5, "Shared foundations").

## Global Constraints

- psycopg 3 async only; SQL in `db/sql/` via `csa.core.db.q`; Pydantic contracts `extra="forbid"`.
- No secrets in source, tests, docs or logs; passwords only via `getpass`; never print a DSN.
- `NO_LOCAL_ENV=1 ENV=ci make lint test` must stay green; use disposable Testcontainers only.
- Migration ranges: lane A `0470`–`0479`, lane B `0480`–`0489`. W0 adds no migration.
- The identity role is `csa_identity`; the bootstrap and the new command refuse any other role.
- Audit action name for the new command: `identity.platform_admin_added`.
- Frontend tests need node 24: `export PATH=/tmp/fourstacks-toolbin:$PATH` on this host.
- Branch: `feat/w0-foundations` from `main`, in a worktree under `.worktrees/`.

## Review Focus

1. Approver with the right password but `is_enabled = false` or not a platform admin: the command must refuse and create nothing (Task 4 test).
2. Target email that already exists as a tenant member: refuse with "already exists"; never silently promote (Task 4 test).
3. Wrong approver password five times: throttle answers 429-equivalent (`HTTPException` 429) before any user lookup on the sixth (Task 4 test).
4. Compose gate given an RDS-style host when `CSA_IDENTITY_EXPECTED_HOST` is set to that host: delegates; with the default it refuses (Task 6 test).
5. A future lane bumping the schema version edits exactly one line: the three test literals are gone (Task 1 grep step).

---

### Task 1: Single schema-version source, migration range README, router block, spec commit

**Files:**
- Modify: `tests/test_migrate.py:19` (remove literal, import from gateway)
- Modify: `tests/test_hr_persistence.py:1419` (assert against shipped versions, not a literal)
- Modify: `apps/gateway/main.py:333-351` (wrap router registrations in a commented block)
- Create: `db/migrations/README.md`
- Add to git: `docs/superpowers/specs/2026-10-27-client-growth-program-design.md`, `docs/superpowers/plans/2026-10-27-w0-foundations.md`

**Interfaces:**
- Produces: `apps.gateway.main.EXPECTED_SCHEMA_VERSION` is the only literal; `shipped_versions()` already exists there.

- [ ] **Step 1: Replace the literal in `tests/test_migrate.py`**

Change line 19 to `from apps.gateway.main import EXPECTED_SCHEMA_VERSION` (keep the import grouped with the other first-party imports; ruff isort will tell you where). Keep `LG01_SQL_VERSIONS`.

- [ ] **Step 2: Replace the literal in `tests/test_hr_persistence.py`**

Replace `assert EXPECTED_SCHEMA_VERSION == "0460"` with:

```python
    from apps.gateway.main import shipped_versions

    assert EXPECTED_SCHEMA_VERSION == max(shipped_versions())
```

- [ ] **Step 3: Confirm no other literal remains**

Run: `grep -rn '"0460"' tests/ apps/ csa/ | grep -v apps/gateway/main.py`
Expected: no output.

- [ ] **Step 4: Wrap router registrations in `apps/gateway/main.py`**

Around the existing `include_router` calls for marketplace, HR and email, add:

```python
    # --- lane registrations: one line per lane; keep alphabetical, no other edits here ---
    app.include_router(build_marketplace_router())
    app.include_router(build_hr_router())
    app.include_router(build_email_router())
    # lane A (customer-facing): WhatsApp router registers here
    # lane B (console): subscriptions router registers here
    # --- end lane registrations ---
```

- [ ] **Step 5: Create `db/migrations/README.md`**

```markdown
# Migrations

Ordered, additive SQL files applied by `scripts/migrate.py` in filename order.
`apps/gateway/main.py::EXPECTED_SCHEMA_VERSION` must equal the highest shipped
version; tests import it from there and never hardcode it.

Reserved ranges (client growth program, 2026-10-27):

- `0470`–`0479`: lane A (WhatsApp channel, support refinements).
- `0480`–`0489`: lane B (subscriptions, HR access page, invitation mail).

Whoever merges a migration raises `EXPECTED_SCHEMA_VERSION` to the highest
merged version. Lanes never reference each other's tables.
```

- [ ] **Step 6: Run the fast checks**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_migrate.py::test_discover_rejects_bad_version tests/test_health.py::test_shipped_versions_includes_expected -q && NO_LOCAL_ENV=1 ENV=ci make lint`
Expected: 2 passed; lint clean.

- [ ] **Step 7: Commit**

```bash
git add tests/test_migrate.py tests/test_hr_persistence.py apps/gateway/main.py db/migrations/README.md docs/superpowers/specs/2026-10-27-client-growth-program-design.md docs/superpowers/plans/2026-10-27-w0-foundations.md docs/superpowers/plans/2026-10-27-a1-whatsapp-channel.md docs/superpowers/plans/2026-10-27-b1-subscriptions-tab.md
git commit -m "chore(foundations): single schema-version source, lane migration ranges, router block, program spec"
```

---

### Task 2: Reserve the `subscriptions` navigation key

**Files:**
- Modify: `CSA_WebConsole/csa-console/web/lib/navigation.ts`
- Test: `CSA_WebConsole/csa-console/web/tests/nav-visibility.test.mjs`

**Interfaces:**
- Produces: `NavKey` includes `"subscriptions"`; `gate.subscriptions = ["subscriptions"]`. No link is rendered yet; lane B adds the entry `{ key: "subscriptions", label: "Subscriptions", href: to("subscriptions") }` together with its page.

- [ ] **Step 1: Write the failing test** (append to `nav-visibility.test.mjs`)

```js
test("the subscriptions key is reserved but renders no link until its page ships", () => {
  const links = navigationFor(staff, active("subscriptions", "customer_support"));
  assert.ok(!links.some((l) => l.key === "subscriptions" || l.label === "Subscriptions"));
});
```

- [ ] **Step 2: Run it to confirm it passes trivially, then make the type change**

Run (from `CSA_WebConsole/csa-console/web`): `export PATH=/tmp/fourstacks-toolbin:$PATH; node --test tests/nav-visibility.test.mjs`
Expected: all pass (this test guards the reservation; the type change below is what matters).

- [ ] **Step 3: Add the key and gate in `lib/navigation.ts`**

Add `| "subscriptions"` to `NavKey` and `subscriptions: ["subscriptions"],` to `gate`. Do not add an `entries` item.

- [ ] **Step 4: Run the isolated frontend check**

Run: `export PATH=/tmp/fourstacks-toolbin:$PATH; npm --prefix CSA_WebConsole/csa-console/web run check:isolated`
Expected: tests, lint, types and build pass.

- [ ] **Step 5: Commit**

```bash
git add CSA_WebConsole/csa-console/web/lib/navigation.ts CSA_WebConsole/csa-console/web/tests/nav-visibility.test.mjs
git commit -m "feat(console): reserve the subscriptions navigation key for lane B"
```

---

### Task 3: Writing guide

**Files:**
- Create: `docs/WRITING_GUIDE.md`
- Modify: `ui-guide/UI_GUIDE.md` (one row in the "Start here" table pointing at the guide)
- Modify: `docs/PROJECT_OVERVIEW.md` ("Start here" table: add a row "Words on screen → docs/WRITING_GUIDE.md")

- [ ] **Step 1: Write `docs/WRITING_GUIDE.md`**

Sections and the rules each must carry (write them as short imperative lines with one good and one bad example each):

1. **Who we write for**: tenant staff who are not technical; platform admins second.
2. **Rules**: sentence case everywhere; buttons start with a verb ("Pause subscription", not "Pause"); say what happens next ("We will email the link"); no internal codes, table names, acronyms or UUIDs on screen (keep raw codes on `ApiError.code` for support); one idea per sentence; Malaysian English spelling (organisation, colour); dates as "27 Oct 2026, 09:15"; money as "RM 59.90"; people are "members", customers are "customers", the AI is "the assistant".
3. **Errors**: lead with what the person can do; name the thing, not the code (`package_inactive` → "Customer support is not switched on for this company. Ask your administrator."). Include the mapping table for the codes staff can meet: `package_inactive`, `planner_unavailable`, `proposal_consumed`, `outside_service_window`, `recipient_unresolved`, `hr_owner_required`, `password_change_required`.
4. **Empty states**: say what will appear here and how to make it appear.
5. **Appendix for developers: working in two lanes**: migration ranges (link `db/migrations/README.md`), the gateway router block, `docs/console_openapi.json` regenerated on `main` only, one `docs/PROGRESS.md` entry per lane at the top, `csa/console/packages.py` untouched by either lane, focused tests locally and full suites on CI.

- [ ] **Step 2: Link it**

Add `| Words on screen | [Writing guide](./WRITING_GUIDE.md) |` to the "Start here" table in `docs/PROJECT_OVERVIEW.md`, and a row `| [Writing guide](../docs/WRITING_GUIDE.md) | Labels, descriptions and error copy |` to the table in `ui-guide/UI_GUIDE.md`.

- [ ] **Step 3: Validate docs**

Run: `git diff --check && grep -c '^## ' docs/WRITING_GUIDE.md`
Expected: no whitespace errors; 5 or more headings.

- [ ] **Step 4: Commit**

```bash
git add docs/WRITING_GUIDE.md docs/PROJECT_OVERVIEW.md ui-guide/UI_GUIDE.md
git commit -m "docs: add the writing guide and the two-lane developer appendix"
```

---

### Task 4: `IdentityService.add_platform_admin`

**Files:**
- Modify: `csa/console/identity.py` (new method after `bootstrap`)
- Test: `tests/test_console_sessions.py` (uses the existing `sessions` fixture; ADMIN_PASSWORD is the bootstrap admin's changed password)

**Interfaces:**
- Consumes: `_throttle(email, ip, *, purpose)`, `_one(conn, "user_email", (email,))`, `_exec(conn, "lock_admin")`, `_one(conn, "create_user", (email, display_name, password_hash, True, True))`, `_audit(conn, actor_id, action, target_id, **details)`, `hash_password`, `verify_password`, `EmailInput`.
- Produces:

```python
async def add_platform_admin(
    self,
    approver_email: str,
    approver_password: str,
    email: str,
    display_name: str,
    password: str,
    *,
    ip: str = "operator-cli",
) -> UUID:
```

Behaviour: normalise both emails with `EmailInput`; `1 <= len(display_name) <= 120` else `ValueError`; `await self._throttle(approver_email, ip, purpose="admin_add")`; load approver by `user_email`; `verify_password` always runs (constant-time, even when no row); refuse with `PermissionError("approver must be an enabled platform administrator with a valid password")` when the row is missing, password invalid, `is_enabled` false, `is_platform_admin` false or `must_change_password` true; then in one transaction: `lock_admin`; if `user_email` for the target returns a row → `ValueError("a user with that email already exists")`; `create_user(email, display_name, hash, True, True)`; `_audit(conn, approver["id"], "identity.platform_admin_added", new_id, approver_email=approver_email)`; return the id. On refusal, audit `identity.platform_admin_add_refused` with `digest(approver_email)` as target in its own short transaction before raising.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_console_sessions.py`)

```python
async def _audit_actions(owner_dsn: str, action: str) -> list[dict[str, Any]]:
    async with await psycopg.AsyncConnection.connect(owner_dsn, row_factory=dict_row) as conn:
        cur = await conn.execute("SELECT actor_id, target_id, details FROM console_audit WHERE action = %s", (action,))
        return await cur.fetchall()


async def test_add_platform_admin_requires_a_verified_enabled_approver(
    sessions: SessionWorld,
) -> None:
    service = sessions.service
    with pytest.raises(PermissionError):
        await service.add_platform_admin(
            "synthetic-admin@example.test", "wrong-password-000", "ops@example.test", "Ops", ADMIN_PASSWORD
        )
    with pytest.raises(PermissionError):
        await service.add_platform_admin(
            "nobody@example.test", ADMIN_PASSWORD, "ops@example.test", "Ops", ADMIN_PASSWORD
        )
    refused = await _audit_actions(sessions.owner_dsn, "identity.platform_admin_add_refused")
    assert len(refused) == 2 and all(row["actor_id"] is None for row in refused)
    assert all("password" not in str(row["details"]).lower() for row in refused)
    async with await psycopg.AsyncConnection.connect(sessions.owner_dsn) as conn:
        count = await (await conn.execute("SELECT count(*) FROM console_users")).fetchone()
    assert count is not None and count[0] == 1


async def test_add_platform_admin_creates_a_forced_change_admin_once(
    sessions: SessionWorld,
) -> None:
    service = sessions.service
    new_id = await service.add_platform_admin(
        "synthetic-admin@example.test", ADMIN_PASSWORD, "Ops@Example.test ", "Ops Admin", MEMBER_PASSWORD
    )
    added = await _audit_actions(sessions.owner_dsn, "identity.platform_admin_added")
    assert [str(row["target_id"]) for row in added] == [str(new_id)]
    assert str(added[0]["actor_id"]) == sessions.admin_id
    assert added[0]["details"] == {"approver_email": "synthetic-admin@example.test"}
    browser = await sessions.browser()
    login = await sessions.mutate(
        browser, "/console/v1/auth/login", {"email": "ops@example.test", "password": MEMBER_PASSWORD}
    )
    assert login.status_code == 200, login.text
    assert login.json()["role"] == "platform_admin"
    assert login.json()["user"]["must_change_password"] is True
    with pytest.raises(ValueError, match="already exists"):
        await service.add_platform_admin(
            "synthetic-admin@example.test", ADMIN_PASSWORD, "ops@example.test", "Again", MEMBER_PASSWORD
        )


async def test_add_platform_admin_throttles_repeated_bad_approver_passwords(
    sessions: SessionWorld,
) -> None:
    service = sessions.service
    for _ in range(service.config.login_attempts):
        with pytest.raises(PermissionError):
            await service.add_platform_admin(
                "synthetic-admin@example.test", "wrong-password-000", "ops@example.test", "Ops", ADMIN_PASSWORD
            )
    with pytest.raises(HTTPException) as blocked:
        await service.add_platform_admin(
            "synthetic-admin@example.test", ADMIN_PASSWORD, "ops@example.test", "Ops", ADMIN_PASSWORD
        )
    assert blocked.value.status_code == 429
```

Add `from fastapi import FastAPI, HTTPException` to the imports. Check `IdentityConfig.login_attempts` is the attribute name used by `_throttle` (it is, see `csa/console/identity.py:256`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_console_sessions.py -k add_platform_admin -q`
Expected: 3 failed with `AttributeError: ... has no attribute 'add_platform_admin'`.

- [ ] **Step 3: Implement the method in `csa/console/identity.py`** with the signature and behaviour above. Use the existing helpers only; no new SQL files are needed (`user_email`, `lock_admin`, `create_user`, `audit` exist).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_console_sessions.py -q`
Expected: all pass (the whole file, to prove the fixture is unchanged).

- [ ] **Step 5: Lint and commit**

```bash
NO_LOCAL_ENV=1 ENV=ci make lint
git add csa/console/identity.py tests/test_console_sessions.py
git commit -m "feat(identity): add_platform_admin with a password-verified approver and audit"
```

---

### Task 5: Operator script `scripts/add_platform_admin.py`

**Files:**
- Create: `scripts/add_platform_admin.py`
- Test: `tests/test_console_identity_config.py` (mirror the two bootstrap CLI tests)
- Modify: `docs/CONSOLE_API.md:118-126` (after the bootstrap paragraph, document the command)

**Interfaces:**
- Consumes: `IdentityService.add_platform_admin` (Task 4), `build_pool`, `IdentityConfig`.
- Produces:

```python
async def run(approver_email: str, approver_password: str, email: str, display_name: str, password: str) -> None
def main() -> None
```

CLI: `--approver-email`, `--email`, `--display-name` (required) and the deliberate flag `--add-platform-admin` (required). `main()` reads, in order, the approver's password (`getpass`, prompt "Approver password: "), then the new admin's initial password twice ("Initial password (at least 12 characters): ", "Confirm initial password: "); mismatched confirmation → `parser.error`. `run()` refuses when `IDENTITY_DATABASE_URL` is unset or its username is not `csa_identity` (same check and message as the bootstrap script), opens a 1-connection pool, calls `check_ready()`, then `add_platform_admin(...)`, prints `Added platform administrator <id>; password change required at first login.` Nothing else is printed.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_console_identity_config.py`)

```python
def test_add_admin_cli_requires_deliberate_flag_before_reading_passwords(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import add_platform_admin

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "add_platform_admin",
            "--approver-email",
            "a@example.test",
            "--email",
            "b@example.test",
            "--display-name",
            "B",
        ],
    )
    prompt = Mock(side_effect=AssertionError("must not prompt before deliberate flag"))
    monkeypatch.setattr(add_platform_admin.getpass, "getpass", prompt)
    with pytest.raises(SystemExit) as error:
        add_platform_admin.main()
    assert error.value.code == 2
    prompt.assert_not_called()


async def test_add_admin_cli_rejects_missing_or_owner_dsn_before_opening_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import add_platform_admin

    pool = Mock(side_effect=AssertionError("must not open an owner or missing identity connection"))
    monkeypatch.setattr(add_platform_admin, "build_pool", pool)
    for value in ("", "postgresql://csa:synthetic@localhost:1/disposable_fixture"):
        monkeypatch.setenv("IDENTITY_DATABASE_URL", value)
        with pytest.raises(ValueError, match="csa_identity"):
            await add_platform_admin.run(
                "a@example.test", "approver-pass-123", "b@example.test", "B", "synthetic-pass-123"
            )
    pool.assert_not_called()
```

- [ ] **Step 2: Run to verify they fail**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_console_identity_config.py -k add_admin -q`
Expected: 2 failed with `ModuleNotFoundError: No module named 'scripts.add_platform_admin'`.

- [ ] **Step 3: Write `scripts/add_platform_admin.py`** following `scripts/bootstrap_console_admin.py` line for line in structure (module docstring stating no dotenv, explicit DSN, passwords via getpass, never printed).

- [ ] **Step 4: Run to verify they pass**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_console_identity_config.py -q`
Expected: all pass.

- [ ] **Step 5: Document in `docs/CONSOLE_API.md`** right after the bootstrap paragraph:

```markdown
Add a further platform administrator only through the operator command; invites
create tenant members, never platform admins:

```sh
NO_LOCAL_ENV=1 uv run python -m scripts.add_platform_admin \
  --approver-email existing-admin@example.test \
  --email new-admin@example.test --display-name "New Admin" --add-platform-admin
```

The approver re-enters their own password (same throttle as login) and must be an
enabled platform administrator who has changed their initial password. The new
account must change its password at first login. Audit: `identity.platform_admin_added`
(approver as actor) or `identity.platform_admin_add_refused`.
```

- [ ] **Step 6: Lint and commit**

```bash
NO_LOCAL_ENV=1 ENV=ci make lint
git add scripts/add_platform_admin.py tests/test_console_identity_config.py docs/CONSOLE_API.md
git commit -m "feat(scripts): add_platform_admin operator command"
```

---

### Task 6: Compose one-shot `admin-add` with a configurable identity target

**Files:**
- Modify: `deploy/compose.staging-local-proposal.yml` (new service `admin-add` after `admin-bootstrap`)
- Modify: `deploy/STAGING_PIPELINE.md` (new section "## 8. Add a platform administrator (owner runs)")
- Modify: `deploy/RELEASE.md:268-274` (add a bullet for `admin-add` next to `admin-bootstrap`)
- Test: `tests/test_staging_compose.py` (service set, profiles, networks; gate behaviour)

**Interfaces:**
- Consumes: `scripts.add_platform_admin` (Task 5).
- Produces: compose service `admin-add`, profile `admin-add-approved`, environment `CSA_ADMIN_ADD_APPROVAL=${CSA_STAGING_ADMIN_ADD_APPROVAL:-disabled}`, `CSA_IDENTITY_EXPECTED_HOST=${CSA_IDENTITY_EXPECTED_HOST:-staging-db}`, `CSA_IDENTITY_EXPECTED_DB=${CSA_IDENTITY_EXPECTED_DB:-csa_staging}`, `CSA_IDENTITY_DSN_FILE=/run/secrets/IDENTITY_DATABASE_URL`, secret `staging_identity_dsn` → `IDENTITY_DATABASE_URL`, network `data` only, `healthcheck: disable: true`, same `<<: *python` anchor. Inline gate (`python -c`): exits with "Explicit isolated admin-add approval required" unless `CSA_ADMIN_ADD_APPROVAL == "approved-isolated-staging"`; reads the DSN file; refuses unless scheme is `postgres|postgresql`, hostname equals `CSA_IDENTITY_EXPECTED_HOST`, port in `{None, 5432}`, username `csa_identity`, path `/` + `CSA_IDENTITY_EXPECTED_DB`, no query or fragment (message contains "target"); never prints the DSN; then `runpy.run_module("scripts.add_platform_admin", run_name="__main__", alter_sys=True)`.

- [ ] **Step 1: Extend the service-set assertions in `test_local_staging_compose_is_valid_and_network_isolated`**

Add `"admin-add"` to the expected service set, `"admin-add": {"admin-add-approved"}` to the profiles mapping, `assert set(rendered["services"]["admin-add"]["networks"]) == {"data"}`, and `"--profile", "admin-add-approved"` to the compose config command in that test.

- [ ] **Step 2: Add a gate test**

Copy `_render_admin_gate` as `_render_admin_add_gate(tmp_path)` (reads `services["admin-add"]["entrypoint"]`, includes `--profile admin-add-approved`). Add:

```python
@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI is unavailable")
@pytest.mark.parametrize(
    ("approval", "dsn", "expected_host", "expected_db", "delegated"),
    [
        ("approved-isolated-staging", "postgresql://csa_identity@staging-db:5432/csa_staging", None, None, True),
        ("approved-isolated-staging", "postgresql://csa_identity@csa.abc.ap-southeast-5.rds.amazonaws.com/csa", "csa.abc.ap-southeast-5.rds.amazonaws.com", "csa", True),
        ("approved-isolated-staging", "postgresql://csa_identity@csa.abc.ap-southeast-5.rds.amazonaws.com/csa", None, None, False),
        (None, "postgresql://csa_identity@staging-db/csa_staging", None, None, False),
        ("approved-isolated-staging", "postgresql://csa@staging-db/csa_staging", None, None, False),
        ("approved-isolated-staging", "postgresql://csa_identity@staging-db/csa_staging?host=postgres", None, None, False),
    ],
)
def test_admin_add_gate_honours_configurable_identity_target(
    tmp_path: Path, approval: str | None, dsn: str, expected_host: str | None, expected_db: str | None, *, delegated: bool
) -> None:
```

Body mirrors `test_admin_bootstrap_gate_requires_approval_and_identity_target` with a stub `scripts/add_platform_admin.py`, env `CSA_ADMIN_ADD_APPROVAL`, and `CSA_IDENTITY_EXPECTED_HOST`/`CSA_IDENTITY_EXPECTED_DB` set only when not None (the rendered code must fall back to `staging-db`/`csa_staging` defaults itself, so the compose default expression must be rendered into the entrypoint env, not the code: use `os.environ.get("CSA_IDENTITY_EXPECTED_HOST", "staging-db")` in the inline code and pass the env var through the service environment). Args: `["--approver-email", "a@example.test", "--email", "b@example.test", "--display-name", "B", "--add-platform-admin"]`. Assert neither `staging-db` nor the RDS host appears in stdout+stderr beyond the word "target".

- [ ] **Step 3: Run to verify they fail**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_staging_compose.py -k "network_isolated or admin_add" -q`
Expected: failures on the missing `admin-add` service.

- [ ] **Step 4: Add the service to `deploy/compose.staging-local-proposal.yml`** as specified in Interfaces.

- [ ] **Step 5: Run to verify they pass**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_staging_compose.py tests/test_staging_pipeline.py -q`
Expected: all pass.

- [ ] **Step 6: Runbook**

Append to `deploy/STAGING_PIPELINE.md`:

```markdown
## 8. Add a platform administrator (owner runs)

Invites create tenant members only. A further platform administrator is added by
an existing one, who re-enters their own password:

```sh
CSA_STAGING_ADMIN_ADD_APPROVAL=approved-isolated-staging \
csa --profile admin-add-approved run --rm --no-deps admin-add \
  --approver-email existing-admin@example.test \
  --email new-admin@example.test --display-name "New Admin" --add-platform-admin
```

Prompts: approver password, then the new initial password twice. Nothing is
printed except the new user id. The new account must change its password at
first login. On another database host (for example RDS) set
`CSA_IDENTITY_EXPECTED_HOST` and `CSA_IDENTITY_EXPECTED_DB` to the expected
values before running; the role must still be `csa_identity`.
```

Add to `deploy/RELEASE.md` after the `admin-bootstrap` bullet: one bullet describing `admin-add` (profile `admin-add-approved`, same network and secret, approval variable `CSA_STAGING_ADMIN_ADD_APPROVAL`, configurable expected host/database, delegates to `scripts.add_platform_admin`).

- [ ] **Step 7: Lint and commit**

```bash
NO_LOCAL_ENV=1 ENV=ci make lint && git diff --check
git add deploy/compose.staging-local-proposal.yml deploy/STAGING_PIPELINE.md deploy/RELEASE.md tests/test_staging_compose.py
git commit -m "ops(staging): admin-add one-shot with a configurable identity target"
```

---

### Task 7: Progress record and final verification

**Files:**
- Modify: `docs/PROGRESS.md` (new top entry "W0 shared foundations (2026-10-27, branch feat/w0-foundations)")

- [ ] **Step 1: Run the focused suites once more on the branch head**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_console_sessions.py tests/test_console_identity_config.py tests/test_staging_compose.py tests/test_staging_pipeline.py tests/test_migrate.py tests/test_health.py -q`
Expected: all pass; record counts and time.

- [ ] **Step 2: Write the PROGRESS entry** (one paragraph): what landed (schema-version source, ranges, router block, nav key, writing guide, `add_platform_admin` + script + compose one-shot), the exact test counts from Step 1, `make lint` result, and that the full suite is CI's job for this PR. Mention that no migration and no runtime behaviour changed.

- [ ] **Step 3: Commit**

```bash
git add docs/PROGRESS.md
git commit -m "docs(progress): record W0 shared foundations"
```

Then use superpowers:finishing-a-development-branch (push and open a pull request against `main`).
