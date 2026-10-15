# Data and integration contracts

All names below are proposed. Discovery must match actual ID types, migration conventions and existing project columns. Do not execute these descriptions as unverified SQL.

## 1. Data ownership and core records

Keep new PM tables in `console_db.app`. New entity IDs can be UUIDs; project/client/user references must match existing types exactly. Server-side timestamps use timezone-aware UTC. Use additive migrations, explicit indexes and realistic row limits.

| Record | Key fields | Invariant |
|---|---|---|
| `app.projects` additions | scope, description, goals, created_by, row_version; reuse existing fields when present | Client scope has client_id; internal scope has none |
| `app.pm_project_members` (phase 09) | project_id, user_id, access_role | Both project and user belong to same tenant for client access |
| `app.pm_milestones` | project_id, title, due_at, status, acceptance_criteria, version | Task/milestone relationships stay within project |
| `app.pm_tasks` | project_id, milestone_id, title, description, status, priority, human_assignee_id, agent_assignment_id, acceptance_criteria, position, version | Distinct lifecycle from project status |
| `app.pm_task_dependencies` | project_id, task_id, depends_on_task_id | No self-link or cycle; both tasks in project |
| `app.pm_task_comments` | project_id, task_id, author_user_id/assignment_id, body, visibility | Typed author and policy-filtered visibility |
| `app.pm_notes` | project_id, stable id, title, folder, current_revision_id, visibility, version | Stable IDs survive rename; folders are metadata |
| `app.pm_note_revisions` | project_id, note_id, body_markdown, author, created_at, content_hash | Immutable revision bodies |
| `app.pm_note_links` | project_id, source_note_id, target_note_id | No cross-project link by accident |
| `app.pm_artifacts` | project_id, kind, title, visibility, current_revision_id | Owner/metadata for diagram, architecture, report, patch or export |
| `app.pm_shared_revisions` (phase 09) | project_id, note/artifact revision_id, reviewed client projection, shared_by, published_at, revoked_at | Explicit selected revision; new edits remain private until published |
| `app.pm_artifact_revisions` | artifact_id, project_id, format, source_ref/body, render_ref, evidence, validation_status, content_hash | Revision immutable; private storage refs not public URLs |
| `app.pm_agent_definitions` | name, role, active, current_version_id | Global catalog owned by platform admins |
| `app.pm_agent_definition_versions` | definition_id, soul_text, profile_json, model_policy_ref, skill_refs, capabilities, hash | Immutable config; never stores project secrets or memory |
| `app.pm_project_agent_assignments` | project_id, definition_id, pinned_version_id, policy_override, enabled | Version pinned per assignment/run |
| `app.pm_memories` | scope_kind, tenant_id/project_id, assignment_id, key, value, source, confidence, expires_at | Valid scope shape and authorized retrieval |
| `app.pm_runs` | project_id, task_id, requester_user_id, mode, status, thread_id, definition_snapshot_refs, context_refs, budgets, usage, idempotency_key, version | Stable project/thread binding; server-created context |
| `app.pm_jobs` | run_id, state, available_at, lease_owner, lease_until, fence, attempt, payload_ref | At most one valid fenced owner per job |
| `app.pm_run_events` | project_id, run_id, sequence, event_id, event_type, public_payload, created_at | Unique run/sequence and event_id |
| `app.pm_proposals` | project_id, run_id, immutable proposal revision/hash, structured body, acceptance_map, status | Exact revision accepted atomically; proposal-local IDs mapped once |
| `app.pm_actions` | run_id, project_id, action_type, canonical_payload, payload_hash, base_revision, policy_snapshot, status, provider_receipt | Ledger for tool side effects |
| `app.pm_approvals` | action_id, actor_user_id, decision, expires_at, consumed_at, version | Scoped, fresh and atomically consumed |
| `app.pm_workspaces` | project_id, repository_ref, base_commit, runner_id, sandbox_locator, status | Locators are server-owned; no arbitrary path input |
| `app.pm_terminal_sessions` | project_id, workspace_id, owner_user_id, runner_pty_id, state, expires_at | Admin and owner checks on every attach |
| `app.pm_repositories` | project_id, approved_remote, default_branch, credential_ref, last_indexed_commit | No raw credentials in row or URL |

Shared revisions reference a note or artifact revision with a validated same-project constraint; use a typed relation or dedicated sharing tables if the ORM cannot safely express the polymorphic reference. Use project_id on child records where it enables composite constraints and scoped queries. A duplicated client_id is optional: if added, its consistency with the parent must be enforced. Project membership, cross-project assignment checks and dependency validation must be transactional.

Task states: `backlog`, `ready`, `in_progress`, `review`, `done`, `cancelled`; blocking is a reason/flag so it does not erase the underlying workflow stage. Prevent `done` without acceptance evidence or a privileged explicit override with audit.

Run states: `queued`, `running`, `waiting_approval`, `waiting_input`, `cancelling`, `cancelled`, `succeeded`, `failed`, `expired`, `needs_reconciliation`. Terminal states differ and are not agent-run states.

## 2. Authorization

A common `resolve_project_access(user, project_id, operation)` first loads a permitted project, checks tenant and scope, applies membership/visibility and operation permissions, then returns the authorized record. Each child lookup verifies `(project_id, child_id)` together. Unauthorized resource access returns the existing application's chosen 403/404 convention consistently.

Admin project writes reuse `require_admin`. Later portal routes infer tenant from CurrentUser; a request body client_id cannot choose the tenant. Agents are scoped service principals acting under a run policy, not browser users and never `platform_admin` sessions. Workers use their own limited DB/service role; only the tool gateway decides which operations they may perform.

## 3. Public API shape

Preserve existing APIs. New routes can be added under the following families, without conflicting with old client/channel routes:

| Route family | Operations |
|---|---|
| `/api/admin/pm/projects` | GET filtered/cursor list, POST create; GET/PATCH `/{pid}` |
| `/api/admin/pm/projects/{pid}/tasks` | GET/POST; PATCH `/{tid}`; POST `/{tid}/move`; dependency and comment endpoints |
| `/api/admin/pm/projects/{pid}/milestones` | List/create/update |
| `/api/admin/pm/projects/{pid}/notes` | List/create; detail/update with revision; backlinks/search |
| `/api/admin/pm/projects/{pid}/artifacts` | List/create; revisions and authorized exports |
| `/api/admin/pm/agent-definitions` | Global definition CRUD and immutable versions |
| `/api/admin/pm/projects/{pid}/agent-assignments` | Attach/detach/pin definition version and policy |
| `/api/admin/pm/projects/{pid}/runs` | POST start, GET list/detail; POST `/{rid}/cancel` |
| `/api/admin/pm/projects/{pid}/runs/{rid}/events` | GET authenticated SSE with replay cursor |
| `/api/admin/pm/projects/{pid}/proposals/{proposal_id}/accept` | Atomic acceptance of selected proposed tasks/artifacts |
| `/api/admin/pm/projects/{pid}/runs/{rid}/approvals/{approval_id}` | POST validated decision; server queues continuation |
| `/api/admin/pm/projects/{pid}/workspaces` | Provision/list allowed workspaces |
| `/api/admin/pm/projects/{pid}/terminal-sessions` | POST create; POST detach/terminate; authenticated WS attach |
| `/api/client/pm/projects/{pid}/...` | Phase 09 wrappers through shared authorization/services |

Use Pydantic input/output contracts, validation bounds, cursor pagination and consistent errors. Do not expose unfiltered DB JSONB. PATCH requires expected `version` or If-Match and returns 409/412 on conflict. Mutating POSTs accept an idempotency key bound to actor/project/operation/body hash; mismatched bodies with the same key conflict rather than silently replaying.

Start-run response: HTTP 202, `{run_id, status:'queued', events_url}`. AI execution is not tied to the HTTP request lifetime. An unavailable optional adapter yields a typed unavailable result and visible failure/degraded state.

## 4. Worker leases and checkpoints

A transaction creates run, job and initial event. Workers claim queued jobs using the repository's PostgreSQL transaction conventions, e.g. `FOR UPDATE SKIP LOCKED`, and increment a fencing token. Heartbeat lease every 10 seconds; initial lease TTL 45 seconds. These are tunable defaults. Polling waits are short and cancellable.

Every consequential tool request carries run_id, action_id and current fence. The tool gateway rejects stale ownership; action uniqueness prevents a newly claimed job from blindly re-executing a prior side effect. A lost lease cancels local work promptly. Do not assume a lease by itself prevents an already dispatched remote command from continuing.

LangGraph runs use server-generated thread IDs persisted on pm_runs, with a durable PostgreSQL checkpointer. No browser can supply an arbitrary existing thread ID to read/replay another project's checkpoint. Persist graph state containing evidence references, scoped task IDs, current plan, approved action references and aggregate budget; do not store raw secrets or every tool transcript in the checkpoint.

Approvals are real domain actions, not merely `Command(resume=True)` from an untrusted client. The API authenticates/authorizes and records a decision; the worker resumes the correct interrupted run after rechecking freshness and payload hash. Nodes may replay on resume: no non-idempotent side effects occur before an interrupt in the same node.

Checkpoints do not guarantee exactly-once external operations. Use an action ledger with provider receipts and idempotency where available. If a worker crashes after dispatch and before receipt, first inspect provider state or sandbox output. If this cannot be determined, mark `needs_reconciliation`; do not auto-repeat an irreversible action.

## 5. Public event contract

Example, schematic IDs are illustrative only:

```json
{
  "event_id": "event-uuid",
  "run_id": "run-uuid",
  "project_id": "existing-project-id",
  "sequence": 42,
  "type": "agent.handoff",
  "created_at": "2026-10-12T07:00:00Z",
  "payload": {
    "from_assignment_id": "investigator-assignment",
    "to_assignment_id": "implementer-assignment",
    "task_id": "task-uuid",
    "summary": "Failure reproduced in an isolated workspace; patch task ready.",
    "evidence_refs": ["artifact-revision-id"]
  }
}
```

Event types include run.queued/started/completed/failed/cancel_requested/cancelled, agent.assigned/message/handoff, tool.started/completed/failed, proposal.created, approval.required/decided, artifact.created, and budget.exceeded. Token deltas can be ephemeral; durable transitions and artifacts must be persisted. Publish events only after their DB transaction commits.

SSE uses HttpOnly session cookies, exact origin/authorization checks, heartbeat, Last-Event-ID/cursor replay and no-store. Disable ingress buffering/compression where it breaks streaming; configure idle timeout. Replay loads events by authorized run/project and monotonic sequence; do not rely on process memory. A retention gap produces a resync-required response. Revalidate session periodically and end streams on revocation/expiry. GET subscription must never itself start a mutation.

## 6. Tool interface

Typed context (server-owned): project_id, run_id, task_id, actor/service principal, approved capability set, workspace_id, base_commit, budget, fence, deadline.

Each registered tool has name, input schema, permission class, limits, output schema, idempotency behavior and audit/event mapping. Initial tools: search_project_notes, read_project_artifact, propose_tasks, generate_diagram. Runner tools later: read_workspace_file, search_workspace, run_tests, create_patch, execute_approved_task. No generic production SQL or arbitrary host-shell tool.

Permission classes: project_read, draft_artifact, board_mutation, sandbox_execute, repository_publish, deploy. Writing a draft artifact under a permitted run is allowed; changing the live task board, publishing a repository change or deploying follows the configured explicit action policy. Untrusted content cannot set the tool context.

## 7. Hermes adapter contract

This is a new integration boundary, not a documented native endpoint. Implement it in-process against a verified SDK/headless interface, or as a private service with equivalent typed methods:

- `capabilities()` → runtime version, structured outputs, cancellation, usage reporting, terminal backend, supported limits.
- `submit(job)` → durable external_job_id and accepted status.
- `status(external_job_id)` → running/waiting/succeeded/failed/cancelled/unknown and safe evidence references.
- `events(external_job_id, cursor)` → resumable public summaries/tool events where supported.
- `cancel(external_job_id)` → requested/confirmed/unsupported; unsupported means still running until proven otherwise.

Job fields: idempotency_key/action_id, pinned definition revision, project_id, task brief, bounded context references, workspace_id/base_commit, approved tools, deadline, max_turns, token budget and fence. Profile paths and credential refs are resolved by the adapter; the model cannot select them.

Result fields: outcome, concise summary, findings with file/span/commit evidence, patch artifact reference, changed files, test commands/exit codes/output refs, usage, warnings, external_job_id and runtime_version. Validate output against schema; malformed output does not become success.

A command-line wrapper must use an argument array, sanitized explicit environment and a isolated runner process; never interpolate an AI prompt into `sh -c`. Verify supported Hermes CLI flags during discovery. If only text output is available, parse into a validated draft result and label missing telemetry; do not pretend it offers native streaming or cancellation.

## 8. Repository and artifact constraints

Remote URLs are allowlisted and normalized; reject embedded credentials, file URLs, option-like inputs, private-network targets outside explicit approval, and unsafe protocols. A workspace service handles clones so the API cannot become an SSRF/path traversal proxy. Disable untrusted hooks/submodule recursion by default; cloning is distinct from executing code.

Artifacts use project-scoped opaque storage keys, hashes and revision metadata. Authorization precedes downloading. Diagram exports stay internal to your infrastructure, with no public renderer submission. Back up DB metadata and artifact bytes consistently.
