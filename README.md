# CSA — LangGraph rebuild

Multi-tenant customer-service automation built with Python 3.12, FastAPI,
LangGraph and PostgreSQL. The adopted staff console is Next.js. The repository
contains implemented runtime code, staged/offline foundations and historical
plans; a feature appearing in a plan or UI is not proof it is live.

## Start here

1. Read [repository rules](AGENTS.md).
2. Read the [project orientation, architecture and workflow](docs/PROJECT_OVERVIEW.md).
3. For continuation, check [current progress](docs/PROGRESS.md) and the linked
   active handoff; read only the relevant [phase plan](docs/langgraph-rebuild/).
4. Inspect executable source, migrations, tests and the API contract before changing
   behavior. Load the matching skill under `.agents/skills/` when applicable.

Detailed references:

- [Long-form architecture and trust boundaries](docs/information.md)
- [Runtime architecture summary](docs/ARCHITECTURE.md)
- [Console API contract](docs/CONSOLE_API.md) and [OpenAPI snapshot](docs/console_openapi.json)
- [Development runbook](docs/RUNBOOK.md)
- [Build-only release contract and gated staging proposal](deploy/RELEASE.md)
- [Operational runbook status/index](docs/runbooks/README.md)
- [LG00–LG15 phase plans](docs/langgraph-rebuild/)

## Current boundaries

M6 is incomplete. Local business reads are not marketplace synchronization or
authoritative inventory. M6B authorization/verifier work remains offline and is
not registered as a callback/runtime flow; live seller eligibility and provider
acceptance remain external gates. The source schema expectation and last approved
persistent-dev schema are different; consult progress and never infer fresh DB
state from a dated record.

Hosted build artifacts have passed recorded integrity and disposable exact-artifact
runtime checks. Public browser/ingress, persistent staging, promotion and production
deployment remain separate, unapproved gates. Preserve `fourstacks.my`/`www`; the
future console selection is `console.fourstacks.my`.

## Development checks

Use only isolated development configuration and synthetic fixtures. Python commands
must not load `.env.local` unless a task specifically requires isolated local-dev
configuration.

```sh
uv sync --frozen
NO_LOCAL_ENV=1 ENV=ci make lint
NO_LOCAL_ENV=1 ENV=ci make test
```

The test suite uses disposable Testcontainers where Docker is available. For
database changes, follow `AGENTS.md` and the relevant schema-change skill; never
run a bare destructive reset against persistent dev or production. The documented
`make db-reset` acceptance uses its disposable-container test.

Only `csa/graph/` may import LangGraph; import-linter enforces this boundary.
