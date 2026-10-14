# Repository guidance

This repository rebuilds the CSA runtime with Python 3.12, FastAPI,
LangGraph, and PostgreSQL. Read the relevant phase plan under
`docs/langgraph-rebuild/` before changing behavior.

## Environment safety

- Develop and test only against the isolated dev Docker Postgres and
  generated seed/fixture data.
- Never connect an agent, test, or local app process to the production
  database, production bot tokens, or production n8n.
- Keep production secrets on the server in its environment or Docker secrets.
  Never place secrets in source, fixtures, logs, docs, issues, or prompts.
- Do not use production data in tests; use synthetic fixtures.

## Engineering conventions

- Use psycopg 3 async APIs only; do not add asyncpg.
- Use Pydantic models for external and persisted data contracts.
- Keep SQL in `db/sql/`; use parameterized queries.
- Only `csa/graph/` may import LangGraph.
- Preserve additive, ordered migrations and staged RLS. Do not enable `FORCE`
  while legacy n8n still uses the owner role.
- Keep dependency versions exactly pinned and update `uv.lock` with dependency
  changes.
- Surface configuration, database, and runtime errors; do not add silent
  fallbacks.

## Dev commands

- `make up`: start the isolated dev Postgres (separate from production csa_db).
- `make db-reset`: recreate the local database from the schema baseline and
  migrations. This is destructive to that local database.
- `make test`: run the test suite.
- `make lint`: run Ruff, formatting, Pyright, and import-linter.
- `make run-dev`: start the local gateway, worker, and delivery processes.
- `make run-gateway`: start only the local gateway process.
- `make stop-gateway`: stop any running dev gateway process on port 18080.
- `make restart-gateway`: stop any running dev gateway and start a fresh one.
- `make down`: stop the isolated dev Postgres (separate from production csa_db).

## Definition of done

Run `make lint test` and `make db-reset` for changes that affect code or the
database. Confirm the relevant phase acceptance criteria with test evidence,
keep `.env.local` untracked, and ensure no production connection or secret was
used.
