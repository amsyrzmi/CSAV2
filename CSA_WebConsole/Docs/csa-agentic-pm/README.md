# CSA Console: agentic project management implementation pack

> Deferred historical reference, not an active CSAV2 task or deployment runbook.
> This is the canonical PM documentation copy; the duplicate Markdown under
> `Docs/agentic-pm/` was removed. Its historical path examples do not establish
> current permissions, database connections or rollout instructions.
> For active work, follow the [CSAV2 migration plan](../../../docs/handoffs/2026-10-15-console-source-migration-plan.md),
> [repository guidance](../../../AGENTS.md) and [current progress](../../../docs/PROGRESS.md).
> Rebase this pack onto CSAV2 before restarting PM work.

Prepared 12 October 2026. Start here. This is a design and implementation handoff, not a claim that your application has been modified or your servers inspected.

## Recommended direction

Extend CSA Console into a project workspace: projects, tasks, knowledge, diagrams, architecture, an AI workroom, and isolated coding workspaces. Keep FastAPI as the authority for identity, permissions and writes. Use a LangGraph worker for orchestration; use Hermes through a replaceable adapter for selected coding and investigation jobs. Reuse agent **definitions**, not private memories, across projects.

Your supplied STATE.md already describes `app.projects`, project channels, cookie sessions, CSRF checks, audit logs and a complete client portal. Preserve those features. Project-management agents belong to a new registry, separate from the customer-service `capability_registry` and existing `/agents` monitoring pages.

## How to use with your IDE AI

1. Extract this folder into the repository as `docs/agentic-pm/`.
2. Give your IDE AI the prompt in `IMPLEMENTATION_RULES.md`, the current repository STATE.md, and `phases/00-discovery.md`. Ask it to finish phase 00 only.
3. Review its repository findings and any unresolved decisions. Server checks are in `MANUAL_RUNBOOK.md` and `checks/`.
4. Implement one phase per branch or reviewable commit. Supply `ARCHITECTURE.md`, `CONTRACTS.md`, `SECURITY_AND_TESTING.md`, and that phase's brief as context.
5. After every phase, update the implementation ledger and STATE.md with what actually shipped, run the required checks, and demonstrate its acceptance scenario.
6. Keep future phases disabled until their dependencies pass. A mock adapter is valid in tests and development; mark it clearly and never show mock success as a real production run.

Suggested IDE instruction:

> Read docs/agentic-pm/IMPLEMENTATION_RULES.md, ARCHITECTURE.md, CONTRACTS.md, SECURITY_AND_TESTING.md, the current STATE.md, and phases/00-discovery.md. Inspect the repository first. Implement only phase 00. Produce a repository findings report, a compatibility map, a proposed minimal diff for phase 01, and the unresolved questions. Do not change the live domain DB, deploy, or start later phases. Use the existing stack and conventions.

For the next phase replace the last part with:

> Implement phase NN only. Follow its prerequisites and acceptance checks. Make the changes, run relevant tests, update the ledger and STATE.md, and report the diff, validation and manual steps. If infrastructure credentials or a server decision is missing, implement and verify the local pieces and record the exact blocked integration; do not fabricate a working service.

## Implementation order

| Phase | Outcome | Depends on | Human work |
|---|---|---|---|
| 00 | Verify repo, DB shape, infrastructure and Hermes installation | Supplied state | Run read-only diagnostics; clarify deployment layout |
| 01 | Admin project hub; client-linked and internal projects | 00 | Review additive migration and backup |
| 02 | Task board, milestones, dependencies, activity | 01 | None beyond acceptance review |
| 03 | Obsidian-style Markdown notes, links and decision records | 01 | Choose artifact storage/backup location |
| 04 | Durable AI planner and task proposals in LangGraph | 02, 03 | Configure model access and worker |
| 05 | Diagrams, architecture models and versioned exports | 03, 04 | Deploy local renderer for PlantUML/export |
| 06 | Admin terminal and isolated project workspaces | 01 | Provision isolated runner and repository access |
| 07 | Hermes worker integration and bug investigation | 04, 06 | Verify Hermes; move selected runtime if ready |
| 08 | Coordinated multi-agent workroom and coding review | 05, 07 | Configure concurrency and budget policies |
| 09 | Carefully scoped client project portal | 02–05, policy checks | Select first pilot client |
| 10 | Production resilience and operational rollout | All enabled features | Backups, restore drill, staged promotion |

Phase 10 is the final hardening pass; foundational access controls, durable storage and safe execution are prerequisites in the earlier phases, not postponed until phase 10.

## Tonight's deliverable

Do not make completing this entire roadmap a condition of tonight's hosting and LangGraph migration. See `MIGRATION_TRACK.md` for its IDE brief and `MANUAL_RUNBOOK.md` for operator cutover steps. A useful first PM slice is phase 01 plus a basic phase 02 board. Keep terminal, Hermes execution and autonomous code changes disabled until the runner exists.

There are two independent rollout tracks: deploy/migrate the existing CSA functions, and ship the new PM module behind flags. They share authentication and infrastructure, but an unfinished PM module must not interrupt customer service.

## Files

- `ARCHITECTURE.md`: service boundaries, data ownership, terminal choices, agent workflow, memory, feature recommendations.
- `CONTRACTS.md`: schema direction, routes, events, tool and adapter interfaces.
- `UI_SPEC.md`: project workspace interaction and component specification.
- `IMPLEMENTATION_RULES.md`: persistent IDE instructions and progress ledger.
- `SECURITY_AND_TESTING.md`: permission matrix, critical tests and rollout checks.
- `MIGRATION_TRACK.md`: provider interfaces, compatibility ledger and cutover acceptance checks.
- `MANUAL_RUNBOOK.md`: your manual deployment and migration steps.
- `SOURCES.md`: official documentation links and verification limitations.
- `phases/00-discovery.md` through `phases/10-production.md`: bounded implementation briefs.
- `checks/inspect-server.sh`, `checks/inspect-repo.sh`, `checks/inspect-db.sql`: read-only context collection.

Defaults such as concurrency, timeouts and retention are initial engineering proposals. Measure and change them after learning the actual homeserver resources, model costs and workload.
