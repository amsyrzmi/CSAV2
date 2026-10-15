# Phase 08 — Multiple agents working on one project

Prerequisites: phases 04–07. Multiple roles do not require multiple permanent servers. Deliver bounded coordinated work, not unrestricted agent group chat.

## IDE AI work

1. Add/select Planner, Architect, Investigator, Implementer, Reviewer and QA definitions with immutable versions and project assignments. Templates contain generic skills and identity; task context and memory remain scoped.
2. Extend LangGraph with dependency-aware routing and explicit assignment ownership. Parallelize only independent read-only investigations initially. Use one writer per checkout; multiple implementation tasks get separate worktrees and a later integration/review gate.
3. Use typed collaboration messages: proposal, question, evidence, handoff, review and final. Require project/task/run/evidence references. Render public summaries and tool results without hidden reasoning. A message itself cannot invoke tools or change permissions.
4. Implement a scheduler with aggregate wall-time/token/cost/concurrency limits. Initial defaults are in ARCHITECTURE.md; expose them to admins. Count retries, reviewer calls and Hermes subagents, not just the supervisor. Prohibit unbounded reciprocal delegation and stop after the configured repair cycles.
5. Review stage checks the diff independently against acceptance criteria and test evidence. Reviewer/QA cannot approve their own privileged repository/deploy actions. A human retains the final publication/deployment policy.
6. Merge artifacts/tasks only after version/base-commit checks; conflicting edits produce a reviewable conflict artifact. Acquire a scoped lock for integration into a review branch. Never silently overwrite a human's uncommitted checkout.
7. Build workroom roster, current assignment, bounded messages, handoff arrows/timeline, artifact links, run drawer, budget and approval queue. Add user intervention: pause at a safe boundary, send scoped guidance, request revision, cancel.
8. Require accepted review/evidence before automated task completion. Separate “patch proposed”, “tests passed in sandbox”, “published to repository”, and “deployed” statuses.

## Acceptance

Run a bug-fixture workflow: Investigator reproduces → Implementer drafts patch → Reviewer rejects an intentionally incomplete fix → one repair → QA verifies → human receives review bundle. Show ownership and evidence for each step. Exhaust a small budget and verify all branches stop. Simulate two tasks editing one file and require conflict review. Confirm global profile edits do not alter already pinned runs, and project B cannot see project A's messages.

## Operator work

Set budgets after measuring the host and a few real jobs. Start with two active runs total and read-only fan-out; increase only after load/cost evidence. Repository publication credentials, if enabled later, are short-lived and distinct from clone credentials.
