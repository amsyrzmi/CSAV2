# CSA — LangGraph rebuild

Customer-service automation core. See `docs/langgraph-rebuild/` for the phase plans
and `docs/versions.md` for the resolved dependency pins.

Docs: [Architecture](docs/ARCHITECTURE.md) · [Runbook](docs/RUNBOOK.md) ·
[Progress](docs/PROGRESS.md) · [Project overview](docs/PROJECT_OVERVIEW.md)

## Continuing development

Start with [current progress and verification gaps](docs/PROGRESS.md), then the
[console migration plan and next-session prompt](docs/handoffs/2026-10-15-console-source-migration-plan.md).
Use [the console API contract](docs/CONSOLE_API.md) and the relevant
[phase plan](docs/langgraph-rebuild/LG00_master_plan.md) before changing behavior.
The [adopted frontend guide](CSA_WebConsole/csa-console/web/README.md) describes the
safe build boundary. Provider-neutral agent preparation is documented in the
[Wave 2/3 handoff](docs/handoffs/2026-10-15-wave23-preparation.md).

Superseded console handoffs, legacy n8n setup/chat briefs and duplicate PM Markdown
have been removed. The canonical
[PM reference pack](CSA_WebConsole/Docs/csa-agentic-pm/README.md) is retained for
deferred work, not as current CSAV2 implementation or deployment guidance.

## Development

```sh
uv sync --frozen
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports
uv run pytest
```

Only `csa/graph/` may import `langgraph` (enforced by import-linter and
`tests/test_import_boundary.py`).
