---
name: csa-inventory-change
description: Design, implement or review CSA-managed inventory authority, reservations, counts, returns, allocation and reconciliation. Use for csa/inventory, inventory schema proposals or stock-writing integration.
---

# Change CSA inventory behavior

Read [AGENTS.md](../../../AGENTS.md), current inventory entries in
[PROGRESS.md](../../../docs/PROGRESS.md), and the M6D sections of the
[M6 roadmap](../../../docs/archive/handoffs/2026-10-15-console-source-migration-plan.md).
Reconcile plans with current contracts, migrations and tests before choosing the next
slice. Preserve the existing distinction between design evidence, test-local SQL,
application schema, runtime wiring and live cutover.

## Preserve inventory invariants

- Scope every account, warehouse, variant, claim and event to one tenant. Never infer
  provider-variant equivalence from a local product row.
- Maintain `available = on_hand - active reservations` and prevent oversubscription
  under concurrent requests. Exact replay is a no-op; changed payload under the same
  command or idempotency key fails.
- Serialize cancellation, fulfilment and return transitions so one terminal outcome
  wins. Restock returns only after receipt and inspection, and only for the recorded
  sellable quantity.
- Fence writes with expected ledger and authority revisions. A pending non-zero count
  freezes new claims; already valid terminal work may make it stale. Rejection clears
  the freeze without changing stock.
- Require a different verified tenant administrator for every non-zero physical-count
  variance or manual on-hand change. Revalidate actor evidence within the transaction;
  a Pydantic actor object alone is not authentication.
- Persist immutable command fingerprints, retained actor evidence, outcomes and audit
  events. Couple ledger, claim and event writes atomically and make safe retry observable.
- Treat provider reconciliation as report-only until market/version-specific quantity
  meaning, mapping and endpoint behavior are verified. Missing, stale, ambiguous and
  unknown observations must remain distinct states.

Do not create a second stock authority. A migration or writer must define how legacy
`stock_qty` and `reserved_qty` stop competing, including writer draining, fencing and a
tested atomic cutover.

## Build evidence in stages

Use the narrowest current layer:

- pure rules: [domain tests](../../../tests/test_inventory_domain.py);
- typed commands and outcomes: [contract tests](../../../tests/test_inventory_contracts.py);
- candidate PostgreSQL behavior: [persistence feasibility](../../../tests/test_inventory_persistence_feasibility.py)
  and [count transaction tests](../../../tests/test_inventory_count_transactions.py);
- shipped identity/schema: [inventory identity tests](../../../tests/test_inventory_identity.py).

Cover boundary quantities, stale versions, exact and conflicting replay, transaction
failures after each coupled write, cross-tenant reuse of synthetic keys and meaningful
races. If work reaches application migrations, use `$csa-db-change`; use
`$csa-verify-change` for final validation. Do not claim writable runtime authority,
provider synchronization or cutover from prototypes or disposable candidate tables.
