---
name: csa-phase-handoff
description: Reconcile CSA source, plans, progress and validation evidence into a precise continuation handoff. Use when closing a slice, preparing another session or updating project status documentation.
---

# Prepare a CSA phase handoff

Create a continuation record that another session can trust without replaying the whole
history. Read [AGENTS.md](../../../AGENTS.md), inspect `git status --short` and relevant
diffs, then read [PROGRESS.md](../../../docs/PROGRESS.md), the active handoff, applicable
[phase plans](../../../docs/langgraph-rebuild/) and the source/tests changed in the slice.

## Reconcile before writing

- Derive current schema expectations, runtime registration and implemented behavior from
  source. Treat dates, test counts and schema numbers in older documents as historical.
- Separate completed behavior from prototypes, proposals, test-local candidate SQL,
  shipped migrations, persistent-dev state, runtime wiring and live acceptance.
- Preserve user decisions, authorization boundaries, known failures and historical test
  evidence. Do not rewrite a failed full run as green because a focused rerun passed.
- Identify the smallest next task that is actionable under the known gates. External
  eligibility, official provider schemas, persistent migration approval and live testing
  should remain explicit gates when unresolved.
- Preserve the dirty worktree. Do not reset, stash, restore or broadly rewrite existing
  files while preparing a handoff.

## Write the continuation

Update the maintained progress/overview/phase documents only when the task calls for it.
A dedicated handoff should contain:

1. where the next session must start and which documents it must read;
2. final implemented behavior and explicit exclusions;
3. affected source, migration and test areas;
4. exact validation commands and results, including failures, skips and whether the full
   suite covers the final changes;
5. environment state and actions deliberately not performed;
6. unresolved gates, risks and one bounded next slice;
7. a copy-ready prompt that preserves scope, worktree changes and safety boundaries.

Avoid duplicating long architecture descriptions or brittle test counts across several
documents. Link to the authoritative plan and keep one current continuation pointer.
Never include secrets, environment values, real customer data or unredacted provider
payloads in a handoff or prompt.

Use `$csa-verify-change` to confirm final evidence before recording it. For documentation-
only handoff edits, validate local links, formatting and the diff; do not start databases
or services solely to validate prose.
