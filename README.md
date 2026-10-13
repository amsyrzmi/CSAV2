# CSA — LangGraph rebuild

Customer-service automation core. See `docs/langgraph-rebuild/` for the phase plans
and `docs/versions.md` for the resolved dependency pins.

Docs: [Architecture](docs/ARCHITECTURE.md) · [Runbook](docs/RUNBOOK.md) ·
[Progress](docs/PROGRESS.md) · [Project overview](docs/PROJECT_OVERVIEW.md)

## Development

```sh
uv sync --frozen
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports
uv run pytest
```

Only `csa/graph/` may import `langgraph` (enforced by import-linter and
`tests/test_import_boundary.py`).
