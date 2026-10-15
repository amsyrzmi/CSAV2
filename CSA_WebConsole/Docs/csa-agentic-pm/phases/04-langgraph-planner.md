# Phase 04 — Durable AI planning and reviewed proposals

Prerequisites: phases 02 and 03; model policy and checkpoint DB configured before real execution. This phase uses native LangGraph/model calls and does not require Hermes.

## IDE AI work

1. Add versioned agent definitions/assignments, scoped memories, runs/jobs/events, proposals/actions/approvals as needed for the planner. Seed a minimal Planner definition without project memories. Use one shared policy registry.
2. Build a separately supervised worker and a PostgreSQL-backed job queue with leases/fencing, heartbeat, retries and cancellation. Persist checkpoints to the configured graph DB. Verify the installed checkpointer API and setup requirements; pin dependencies in existing lockfiles.
3. Build typed graph stages: resolve scoped context → assess missing information → draft plan → validate structured plan → publish proposal → wait for review → finalize. Missing context yields a waiting_input state; it does not invent a repository or requirements.
4. Plan schema: goals, assumptions, milestones, tasks, dependencies by stable proposal-local IDs, acceptance criteria, risks, estimates labeled uncertain, and source revision references. Validate structure, bounded size and dependency acyclicity. Allow at most two schema-repair attempts.
5. POST start returns 202 and a durable run ID. Add authenticated, resumable SSE and state queries. UI offers selected context and budget, a visible run timeline, cancel, and a proposal editor.
6. Proposal acceptance validates the exact proposal revision and selected items, fills accepted dependencies consistently, and creates tasks/milestones atomically once. Dependencies on unselected proposals require selection, a mapped existing task or explicit removal; never leave dangling references.
7. Approval/resume decisions pass through the domain permission service. Use stable thread IDs, durable interrupts where appropriate and an action ledger for mutations. Replayed nodes cannot double-create tasks.
8. Add token/wall-time limits and usage reporting. Provider credentials remain server-side. Tests use deterministic stub models; a staging real-model smoke test is labeled separately.

## Acceptance

Generate a plan from project notes; edit and accept three tasks; repeat the acceptance request and see no duplicates; request a different body with the same idempotency key and see conflict. Restart the worker while a run waits for review; resume the original thread. Reconnect SSE and recover events without duplicates or cross-project access. Cancellation and budget exhaustion visibly stop permitted work. A model outage leaves manual boards usable. A prompt in a note cannot change tenant scope, reveal secrets or grant shell access.

## Operator work

Create the checkpoint DB/role privately and configure its connection. Configure one model provider and a modest budget. Run the library's verified schema setup once through your migration/release procedure. Enable planner for an internal staging project first. No live csa_db write grants are added.
