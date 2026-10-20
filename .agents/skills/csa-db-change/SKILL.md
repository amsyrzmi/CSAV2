---
name: csa-db-change
description: Design or implement CSA PostgreSQL migrations, tenant-scoped SQL, RLS policies, privileges and schema-readiness changes. Use when changing db/migrations, db/sql or persisted data contracts.
---

# Change the CSA database safely

Read [AGENTS.md](../../../AGENTS.md), the relevant phase plan, and
[LG11](../../../docs/langgraph-rebuild/LG11_schema_changes.md) before editing. Inspect
the dirty worktree and preserve unrelated changes. Current source, migration files and
tests take precedence over dated schema numbers in handoffs.

## Design the change

- Use the next ordered migration and keep it additive while legacy n8n uses the owner
  role. Do not enable `FORCE` RLS. Record preconditions and rollback guidance where the
  migration convention requires them.
- Put runtime SQL in `db/sql/` and bind every value. Use psycopg 3 async APIs through
  the existing database helpers; do not add inline application SQL or asyncpg.
- Model external and persisted boundaries with Pydantic. Keep tenant identifiers and
  authority evidence server-owned rather than accepting them from model or browser data.
- For every tenant table, decide RLS, grants, owner behavior, tenant-composite integrity
  and onboarding/seed effects explicitly. Revoke inherited privileges when a table is
  intentionally read-only.
- Preserve idempotency and atomicity. Business state, operation receipts, audit events
  and outbox records that describe one outcome should commit or roll back together.
- Update gateway schema readiness and migration-test expectations when a new migration
  changes the shipped schema. Search for the previous expected version rather than
  assuming there is one authoritative constant.

For constraints on populated tables, use a compatibility sequence appropriate to the
actual data and locking risk. `NOT VALID` still enforces new writes, so do not describe it
as consequence-free. Concurrent indexes require the migration runner's autocommit path.

## Prove the database behavior

Use synthetic data and disposable Testcontainers only. Add focused tests that cover the
properties affected by the change:

- baseline-to-latest application and clean reapplication;
- failure rollback, including absence of an incorrect migration ledger stamp;
- expected owner, grants and denied mutations for app/read-only roles;
- no tenant GUC, correct tenant and wrong tenant behavior;
- exact replay, conflicting replay and concurrent contenders for mutable workflows;
- preservation of representative legacy rows when compatibility matters;
- readiness rejection for a missing historical or latest migration.

Use [migration tests](../../../tests/test_migrate.py), [RLS tests](../../../tests/test_rls.py),
[tenancy tests](../../../tests/test_tenancy.py), [health tests](../../../tests/test_health.py)
and domain-specific tests as starting points. Follow `$csa-verify-change` for final
validation and evidence reporting.

Do not run `make db-reset` against persistent dev as part of this workflow. The existing
disposable reset test exercises the real target with both database clients pinned to its
container. Applying a migration to persistent dev, restarting services or deploying it
requires the authorization applicable to that separate action.
