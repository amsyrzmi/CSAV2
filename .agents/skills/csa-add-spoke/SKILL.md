---
name: csa-add-spoke
description: Add or extend a CSA spoke agent with typed inputs, fact-only outputs, risk classes, capability registration and golden coverage. Use for new csa/agents capabilities or actions.
---

# Add a CSA spoke agent

Read [AGENTS.md](../../../AGENTS.md), [LG02](../../../docs/langgraph-rebuild/LG02_contracts_state_registry.md),
[LG05](../../../docs/langgraph-rebuild/LG05_spokes_and_tools.md), and the phase plan for
the business capability. Inspect [the shared interface](../../../csa/agents/base.py),
existing agents and their tests before choosing a layout.

## Implement the capability

- Define strict Pydantic inputs with useful field constraints. IDs, tenant scope,
  contacts and execution authority come from `AgentContext` or verified server state,
  never from model claims.
- Implement the shared agent protocol and return `AgentResult` containing typed facts,
  UI and stable error codes. Business truth belongs in facts; do not ask the model to
  invent business claims that a query or deterministic rule can supply.
- Declare per-action `read`, `draft` or `commit` risk accurately. Initial plans may draft;
  the observe loop can only read; commits require the established customer callback or
  staff authority path.
- Put parameterized SQL in `db/sql/<area>/`, preserve tenant transactions, idempotency,
  atomic state changes and durable outbox behavior. Callback tokens contain opaque IDs,
  not model-generated business identifiers.
- Register the capability through the existing ordered migration/onboarding mechanism.
  Keep input schema, routing hint, side effects, timeout, retry policy and implementation
  metadata synchronized. Enable it per tenant; do not hard-code prompt branches or IDs.
- Only `csa/graph/` may import LangGraph. Keep agent code independent of graph internals.

## Demonstrate the behavior

Add focused unit/integration tests and meaningful golden cases from
[LG08](../../../docs/langgraph-rebuild/LG08_testing_seed_evals.md). Include success,
missing input, domain conflict, unauthorized callback, replay/double tap and dependency
failure as applicable. Mutations should prove final database state, event/audit rows and
outbox effects, including concurrent identical requests when idempotency matters.

Confirm registry visibility and disabled/circuit-open behavior without relying on a cache
for execution authorization. Update contract snapshots only for intentional public shape
changes. Use `$csa-db-change` for schema work and `$csa-verify-change` for final evidence.
