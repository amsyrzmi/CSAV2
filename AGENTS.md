# Repository guidance

This repository rebuilds the CSA runtime with Python 3.12, FastAPI,
LangGraph, and PostgreSQL. Read `docs/PROJECT_OVERVIEW.md`, then the relevant
plan or handoff linked from `docs/PROGRESS.md`, before changing behavior. The
original LG00-LG15 phase plans were removed from the repo (see git history); the
rules they set that are still enforced are under "Migration and contract rules".

## Environment safety

- Develop and test only against the isolated dev Docker Postgres and
  generated seed/fixture data.
- Never connect an agent, test, or local app process to the production
  database or production bot tokens.
- Keep production secrets on the server in its environment or Docker secrets.
  Never place secrets in source, fixtures, logs, docs, issues, or prompts.
- Do not use production data in tests; use synthetic fixtures.

## Engineering conventions

- Use psycopg 3 async APIs only; do not add asyncpg.
- Use Pydantic models for external and persisted data contracts.
- Keep SQL in `db/sql/`; use parameterized queries.
- Only `csa/graph/` may import LangGraph.
- Preserve additive, ordered migrations and staged RLS. Do not enable `FORCE`.
- Keep dependency versions exactly pinned and update `uv.lock` with dependency
  changes.
- Surface configuration, database, and runtime errors; do not add silent
  fallbacks.

## Migration and contract rules

- Migrations are forward-only files in `db/migrations/`, numbered in order, applied
  by `scripts/migrate.py` as the owner role, each file and its ledger stamp in one
  transaction. Because of that, `CREATE INDEX CONCURRENTLY` cannot be used. Never
  edit an applied migration; add a new one. Role passwords and other secrets come
  from the deploy environment, never from a migration or git.
- On a populated table, add a constraint as `NOT VALID`, check or fix the data,
  then `VALIDATE CONSTRAINT`. `NOT VALID` still enforces new writes.
- Every new table with a `client_id` needs the tenant-isolation RLS policy (`ENABLE`,
  not `FORCE`), explicit grants for `csa_app` and `csa_ro`, and a test that it returns
  nothing without the tenant GUC and nothing for another tenant. When a migration
  changes the shipped schema, update the gateway's expected schema version and the
  migration tests.
- Do not drop `enqueue_webhook_delivery`, `keep_escalated_status`,
  `contact_identity_tenant_check` or `trg_sync_order_items`; application code relies
  on the rules they encode.
- Agents take typed Pydantic inputs and return `AgentResult`, where facts are the only
  business truth. The `Input` class in code is the source of truth for a capability's
  `input_schema`; sync it to the registry row (`CapabilityRegistry.sync_input_schema`)
  rather than editing the row by hand. `risk_class` (`low` read, `medium` reversible
  write, `high` irreversible or monetary) drives retries and approvals.
- Customer buttons carry only `a:<pending_actions.id>`; consuming an action is one
  conditional `UPDATE ... RETURNING`, so a double tap cannot run twice, and a typed
  "yes" never confirms anything. `Envelope.meta.pii_map` is never logged or sent to a
  model. Keep turn state small (ids and summaries); `thread_id` is the turn's `trace_id`.

## Dev commands

- `make up`: start the isolated dev Postgres (separate from any production database).
- `make db-reset`: recreate the local database from the schema baseline and
  migrations. This is destructive to that local database.
- `make test`: run the test suite, skipping tests marked `slow` (packaged image
  builds). `make test-all` runs everything, as CI does.
- `make lint`: run Ruff, formatting, Pyright, and import-linter.
- `make run-dev`: start the local gateway, worker, and delivery processes.
- `make run-gateway`: start only the local gateway process.
- `make stop-gateway`: stop any running dev gateway process on port 18080.
- `make restart-gateway`: stop any running dev gateway and start a fresh one.
- `make down`: stop the isolated dev Postgres (separate from any production database).

## Definition of done

Run `make lint test` and `make db-reset` for changes that affect code or the
database. Confirm the relevant plan's acceptance criteria with test evidence,
keep `.env.local` untracked, and ensure no production connection or secret was
used.
