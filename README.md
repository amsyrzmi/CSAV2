# CSA — LangGraph rebuild

Customer-service automation core. See [the phase plans](docs/langgraph-rebuild/)
and the authoritative [dependency pins](pyproject.toml) / [resolved lockfile](uv.lock).

Docs: [Architecture](docs/ARCHITECTURE.md) · [Runbook](docs/RUNBOOK.md) ·
[Progress](docs/PROGRESS.md) · [Project overview](docs/PROJECT_OVERVIEW.md)

## Continuing development

Start with the [M5 Telegram inbox scope and evidence](docs/handoffs/2026-10-17-m5-telegram-inbox.md)
and [current progress and verification gaps](docs/PROGRESS.md), then the
[console migration plan](docs/handoffs/2026-10-15-console-source-migration-plan.md).
M4 is functionally closed with an explicitly accepted laptop-dev performance exception.
M5's Telegram staff inbox/reply-approval slice was reviewed and rolled out on approved
guarded dev at **0300**, with platform-admin UI and synthetic Telegram acceptance.
M5 validation: **692 full tests passed** plus lint/frontend checks; live reader denial replay passes.
Representative tenant-admin/staff browser replay also passes. Future rollouts require scoped
approval. M6 now has an [expanded seller-workspace roadmap](docs/handoffs/2026-10-15-console-source-migration-plan.md#m6--business-tabs-integrations-and-staff-preparation)
and an offline read-only local orders/catalogue/stock foundation; it has not replaced
the last approved M5 build. A current probe found the dev gateway/frontend unavailable;
restoration requires separate approval. Historical failures remain recorded. The
[runtime recovery handoff](docs/handoffs/2026-10-16-runtime-stabilization.md)
retains incident history and environment safety details.
M6A lint, targeted backend and frontend checks pass; its full run was **695 passed /
1 TCP latency failure**, not green. Exact unchanged-threshold evidence is in
[current progress](docs/PROGRESS.md).
M6B continues offline with validated TikTok order pages and sanitized failures;
current provider compatibility and tenant/store binding remain explicit gates.
M6B targeted checks: **41 passed**; full lint passes, full tests **732 passed /
1 TCP latency failure** (p95 **608.321 ms**, unchanged **<200 ms**). No rollout or
performance waiver is implied.
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
