---
name: csa-verify-change
description: Validate CSA code and database changes against phase acceptance criteria using synthetic fixtures, disposable PostgreSQL and isolated console checks. Use for patch verification and reporting validation evidence.
---

# Verify a CSA change

Produce validation evidence for the user's current change. Use the repository's
existing tools and respect the session's scope and authorization. This skill does
not authorize persistent database changes, service restarts, deployment or live
provider calls.

Resolve commands and repository paths from the repository root. Links below are
relative to this skill's directory.

## Establish scope and current state

1. Read [AGENTS.md](../../../AGENTS.md) and any instructions beneath the affected
   directory. Inspect `git status --short` and relevant diffs, including untracked
   task files. Preserve existing changes; do not reset, stash, restore or broadly
   reformat the worktree to make validation easier.
2. Read the relevant part of [PROGRESS.md](../../../docs/PROGRESS.md), its linked
   handoff and the applicable [phase plan](../../../docs/langgraph-rebuild/).
   Extract the acceptance criteria for the requested slice. Treat earlier handoffs
   and test counts as dated evidence; inspect current source for schema versions,
   dependency pins and actual runtime registration.
3. Choose checks that prove observable behavior: persisted outcomes, tenant and
   actor restrictions, replay behavior, atomic rollback or public contract shape.
   Add regression tests only where they provide meaningful missing evidence.

## Select relevant checks

Read only the references needed for the changed area:

| Changed area | Validation focus and existing entry points |
| --- | --- |
| Database or SQL | Follow [LG11](../../../docs/langgraph-rebuild/LG11_schema_changes.md). Check additive ordering, apply/reapply, transactional rollback, role privileges and tenant isolation. Use [migration tests](../../../tests/test_migrate.py), [RLS tests](../../../tests/test_rls.py), [tenancy tests](../../../tests/test_tenancy.py) and affected repository tests. Compare shipped migrations with gateway readiness and [health tests](../../../tests/test_health.py). |
| Agent contracts or orchestration | Follow [LG02](../../../docs/langgraph-rebuild/LG02_contracts_state_registry.md), [LG04](../../../docs/langgraph-rebuild/LG04_coordinator_graph.md) or [LG05](../../../docs/langgraph-rebuild/LG05_spokes_and_tools.md) as applicable. Check typed inputs/results, capability schema consistency, action risk and callback authority. Use [contract tests](../../../tests/test_contracts.py), affected agent/runner tests and [golden cases](../../../tests/golden/). |
| Console API or frontend | Follow [LG15](../../../docs/langgraph-rebuild/LG15_console_turnover.md) and [CONSOLE_API.md](../../../docs/CONSOLE_API.md). Check session-derived identity, tenant/role restrictions, CSRF, public projections and transport/UI states. Use affected console tests and the [OpenAPI snapshot test](../../../tests/test_console_contract_snapshot.py). For frontend changes, read the [frontend instructions](../../../CSA_WebConsole/csa-console/web/AGENTS.md) and [verification guidance](../../../CSA_WebConsole/csa-console/web/README.md). |
| Inventory | Read the current M6D sections in the [M6 roadmap](../../../docs/handoffs/2026-10-15-console-source-migration-plan.md). Select from [contract](../../../tests/test_inventory_contracts.py), [domain](../../../tests/test_inventory_domain.py), [identity](../../../tests/test_inventory_identity.py), [persistence feasibility](../../../tests/test_inventory_persistence_feasibility.py) and [count transaction](../../../tests/test_inventory_count_transactions.py) tests. Check conservation, exact/conflicting replay, pending freezes, stale versions, distinct-admin review and coupled-write rollback where relevant. |
| Marketplace authorization or reads | Read the current M6B roadmap sections and [verifier handoff](../../../docs/handoffs/2026-10-18-m6b-verifier-next-session.md), reconciled with current progress. Select affected `test_ecommerce_*` and `test_tiktok_*` files. Use mocked HTTP and disposable PostgreSQL to check exact tenant/store/app binding, credential revisions, expiry, single-use attempts and secret-free errors. |
| Graph policy or performance | Follow [LG08](../../../docs/langgraph-rebuild/LG08_testing_seed_evals.md). For policy changes include `NO_LOCAL_ENV=1 ENV=ci make coverage`. Use `NO_LOCAL_ENV=1 ENV=ci make load-test` when performance evidence is needed; keep timing acceptance runs separate from coverage instrumentation. |

For an intentional public console API change, regenerate the published contract
with `NO_LOCAL_ENV=1 ENV=ci uv run python -m scripts.export_console_openapi` and
review the diff. Do not regenerate a snapshot merely to conceal a regression.

## Execute isolated validation

Use synthetic fixtures and the existing [Testcontainers setup](../../../tests/conftest.py).
Run Python checks with `NO_LOCAL_ENV=1 ENV=ci` so Make and development settings do
not load `.env.local`. Do not inspect environment-file contents or print secrets
to troubleshoot a failure.

Start with the relevant focused pytest selection. For code or database changes,
complete the repository's required full check:

```sh
NO_LOCAL_ENV=1 ENV=ci make lint test
```

Satisfy the required `make db-reset` verification through the existing
`tests/test_migrate.py::test_make_reset_only_disposable_container` test. It invokes
the actual reset target with both psql and Python bound to its disposable fixture
database. Confirm it passed rather than skipped or deselected; the full suite
already includes it, so a successful run need not be repeated. Never substitute
a bare reset against persistent dev. If the user's current instructions forbid
even disposable resets, respect that restriction and report the check as not run.

For adopted frontend changes, run this from the repository root:

```sh
npm --prefix CSA_WebConsole/csa-console/web run check:isolated
```

This uses the existing source allowlist, synthetic transport/state checks,
lint, types, build and trace checks. A build pass is not authenticated browser
acceptance. If dependencies are missing, follow the frontend README's reviewed
installation procedure within the current task's authorization.

For instruction or documentation-only changes, validate structure, local links
and the diff. Do not start databases or services solely to validate prose. For a
skill change, also use the available skill-creator validator.

Surface failed commands and unavailable prerequisites. Fix failures within the
authorized task, then rerun affected checks. Preserve acceptance thresholds and
checks; do not retry timing tests until a passing sample hides an earlier result.
If a required check conflicts with a current restriction, record the missing
evidence explicitly rather than expanding scope.

## Report evidence precisely

Summarize the behavior checked, commands, results and remaining limitations:

- Distinguish passed, failed, skipped, deselected and not-run checks. State whether
  full-suite evidence covers the final changes or predates a later focused fix.
- Attribute a failure to pre-existing work or host contention only with evidence.
  Preserve earlier failures when reporting a useful isolated rerun.
- Separate pure domain checks, test-local candidate SQL, application migrations,
  runtime wiring and live acceptance. An inventory prototype or mocked provider
  composition establishes only the behavior it actually exercises.
- Keep ingress acknowledgement latency, backlog throughput, completed model turns
  and external delivery outcomes distinct. One passing run establishes neither
  stable capacity nor exactly-once external delivery.
- Report any unmet phase acceptance criteria. Update progress or handoff documents
  when the task requires it; retain historical evidence and avoid copying fixed
  schema versions or test counts into reusable skill instructions.
