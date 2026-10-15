# Phase 02 — Tasks, Kanban, milestones and activity

Prerequisite: phase 01. This is the second feature build phase. It is usable without AI. Keep client PM access disabled while preserving existing client projects.

## User outcome

An admin opens a project, creates milestones and tasks, moves tasks through a board, records dependencies and acceptance criteria, and sees an auditable history. The same task service will later accept reviewed AI proposals.

## IDE AI work

1. Add app.pm_milestones, pm_tasks, pm_task_dependencies and pm_task_comments using the verified ID types. Enforce project-scoped relationships. Include version and stable order/position fields.
2. Implement list/create/update/move, dependency management and comments under the route families in CONTRACTS.md. Filter and paginate on the server. Validate titles, descriptions, priorities and timestamps.
3. Implement task transitions: backlog → ready → in_progress → review → done; allow documented backward moves for rework and cancellation with a reason. Dependencies prevent a task from being made ready or started unless completed, or an authorized manual override records its reason. A blocked badge retains its original column.
4. Check dependency cycles inside a transaction and serialize competing dependency edits for the project, so simultaneous requests cannot each pass validation and create a cycle. Validate assignees against active users and project scope.
5. Protect concurrent edits with row versions. Perform board moves and position updates atomically; choose a simple locked/renumbered ordering strategy first. Moving across projects through a move request is forbidden.
6. Add a paginated project activity feed from audited PM events. Preserve immutable audit records; public activity is a curated projection, not the raw audit table.
7. Build board and list views, milestone filter, task drawer and inline creation. Reuse existing kit components. Use an established drag/drop dependency only if needed and compatible; offer keyboard and click alternatives. Optimistic UI must roll back on API error.
8. Add links for future note/artifact/run references without presenting unimplemented actions as functional. Empty assignee/milestone states should be useful.

## Suggested input contracts

Task create: title, description, priority, milestone_id?, human_assignee_id?, acceptance_criteria[], labels[]. Project ID is in the authorized route context. Task edit/move: expected_version plus changed fields; move includes target_status and target_position, never an arbitrary workspace path or tenant ID.

## Acceptance checks

- In an internal project create a milestone and five tasks; move one with mouse and keyboard; refresh and see identical order.
- A blocking dependency prevents starting the dependent task; completing the predecessor removes the block.
- Self-dependency, cross-project dependencies and cycles—including concurrent cycle creation—are rejected.
- Two editors updating one task receive an explicit stale-version conflict rather than losing changes.
- Marking done requires evidence/criteria acknowledgment or an audited authorized override.
- Non-admin requests fail even when task IDs are guessed. Foreign project child IDs do not resolve through an authorized project route.
- API failure returns the card to its previous place and preserves unsaved drawer edits.
- Project lifecycle and channel endpoints still work; write tests use the app test DB only.

## Operator work

Apply the additive app migration in staging after a backup. Run the acceptance scenario with an internal test project. No model credentials, Hermes migration or live domain grants are needed. Do not infer that this phase alone implements agentic execution.
