# CSA Agentic PM feature test checklist

Use an isolated test database, internal test project, two test client tenants, and disposable repositories. Never run write or destructive scenarios against production customer data. Record the build/commit, feature flags, environment, tester, date, result, and evidence for each run.

The implementation ledger marks phases 01–08 `implemented_local`; this is not proof of staging or production readiness. Phase 09 has no test/build or migration execution recorded, and phase 10 still needs staging operations drills. Test optional features only after their dependencies and flags are enabled in the target environment. Keep disabled features visibly unavailable.

## 0. Project navigation and workspace tabs

- [ ] From Projects Hub, expand and collapse the sidebar arrow; verify it works with mouse and keyboard and does not navigate when toggled.
- [ ] Open a project and confirm the menu lists Overview, Task Board, Notes & Knowledge, Activity, Diagrams & Architecture, AI Workroom, and Terminal.
- [ ] Open each view from the Projects sidebar submenu; verify it opens a distinct page route (`/projects/<id>/<view>`) with matching content and no feature tab strip in the page body.
- [ ] Copy a feature URL into a new tab, reload it, and use browser back/forward; verify project ID and selected page remain correct. Confirm older `?tab=` URLs still resolve.
- [ ] Switch between two projects and verify each view shows only that project's data.
- [ ] Enter an unknown `tab` value and verify the page safely falls back to Overview.
- [ ] On the project list, expand Projects and confirm the “open a project” guidance; open a project and confirm its feature links appear.
- [ ] Verify narrow/mobile navigation closes after choosing a feature; verify collapsed sidebar remains usable and accessible.

## 1. Project hub and permissions (Phase 01)

- [ ] List, search, and filter projects by client/internal scope and setup/active/archived status; verify counts and empty states.
- [ ] Create an internal project and a client project for an existing tenant; reject missing client selection and unavailable client lookup.
- [ ] Edit project name, description, goals, and lifecycle; verify version increments and stale concurrent edits show a conflict with refresh.
- [ ] Verify client projects/channels keep stable IDs and existing portal links after migration; verify old endpoints still work.
- [ ] Confirm client A cannot list/open client B or internal projects through UI, API, search, or guessed IDs; tenant APIs cannot create internal projects.
- [ ] Verify duplicate-slug rules by scope, archived project behavior, channel bindings, and audit entries for privileged writes.
- [ ] Confirm internal projects do not affect client counts or client/agent metrics.

## 2. Task board, milestones, dependencies, activity (Phase 02)

- [ ] Create a milestone and tasks with priority, owner, labels, estimates, acceptance criteria, and comments; edit and refresh to confirm persistence.
- [ ] Move tasks through Backlog → Ready → In progress → Review → Done using drag, keyboard, and click alternatives; verify ordering after refresh.
- [ ] Verify blocked tasks retain their column and cannot start until dependencies finish; test authorized override with required reason.
- [ ] Reject self-dependency, cross-project dependency, dependency cycle, and concurrent edits that would introduce a cycle.
- [ ] Open the same task in two sessions; confirm stale updates produce a conflict without silently overwriting values.
- [ ] Verify completion requires criteria/evidence acknowledgement or a recorded authorized override; verify failed move rolls back optimistic UI.
- [ ] Confirm activity is paginated, project-scoped, and curated; unauthorized users cannot access tasks via guessed IDs.

## 3. Notes and project knowledge (Phase 03)

- [ ] Create, edit, search, tag, and move notes between folders; exercise project brief, requirement, bug report, meeting note, and ADR templates.
- [ ] Link notes using stable IDs; rename a linked note and verify backlinks and neighborhood graph still resolve.
- [ ] Test ambiguous, unresolved, and cross-project wiki links; verify they do not leak or resolve to another project.
- [ ] Verify autosave, revision history, restore-as-new-revision, and concurrent edit conflict handling.
- [ ] Search project A and confirm no project B results or snippets appear.
- [ ] Try unsafe HTML, executable Markdown, unsafe URLs, oversized content, and path traversal; confirm safe preview and rejected input.
- [ ] Verify task-note references point to notes in the authorized project and export/import validates names, size, and scope.

## 4. LangGraph planner and proposals (Phase 04)

- [ ] Start a plan from selected project goals/notes; confirm visible context, budget, role, and run ID before execution.
- [ ] Check output includes goals, assumptions, risks, milestones, tasks, dependencies, acceptance criteria, estimates marked uncertain, and evidence references.
- [ ] Submit missing context, malformed output, cyclic dependencies, and oversized proposals; confirm wait-for-input or bounded repair/error behavior.
- [ ] Edit a proposal, accept selected items, and verify milestones/tasks are created atomically exactly once; retry the same idempotency key and test conflicting payload rejection.
- [ ] Restart the worker during a run awaiting review; resume the same durable run/thread without duplicate events or tasks.
- [ ] Disconnect/reconnect SSE and verify ordered event recovery without cross-project replay; test cancellation acknowledgement and budget exhaustion.
- [ ] Simulate model/checkpoint outage; manual project work must remain usable and AI status must be honest.
- [ ] Put prompt-injection instructions in a note; confirm they cannot change tenant/project scope, disclose secrets, or grant tools.

## 5. Diagrams and architecture (Phase 05)

- [ ] Create, edit, save revisions, reopen, and export supported Mermaid sequence and ERD diagrams; also validate class diagram support.
- [ ] Test architecture nodes, typed edges, boundaries, inspector, layout changes, zoom/minimap/search, and semantic-version behavior.
- [ ] Test invalid syntax, malicious Mermaid config, raw HTML, external includes, oversized graph, renderer timeout, and unsafe SVG export.
- [ ] If private PlantUML renderer is enabled, validate a deployment fixture and its resource/network limits; otherwise confirm unsupported types are clearly identified.
- [ ] Generate an application and AWS proposal; inspect assumptions, evidence, trust/data boundaries, failure modes, and dated sources for any costs.
- [ ] Verify no project source is sent to a public rendering service and generation never provisions cloud infrastructure.

## 6. Runner, workspace, terminal (Phase 06)

- [ ] Verify runner capability/health states and create a workspace only from registered project/repository/base commit selections.
- [ ] Open project A and B workspaces and prove each cannot read the other's files or reach console/domain DB services.
- [ ] Test repository URL validation, path traversal, symlink/mount escapes, hooks/submodule execution, and credential scope.
- [ ] Create, resize, detach, reattach, and terminate PTY sessions; verify reconnect does not replay commands and termination ends the actual process.
- [ ] Reject forged Origin, another user's PTY, expired/replayed ticket, revoked session, oversized/rate-flooded frames, and unauthorized terminal creation.
- [ ] Change projects while a terminal is open and confirm the session remains bound to its original workspace.
- [ ] Verify runner offline/degraded state is clear, shell location is explicit, and transcript access/retention is restricted.

## 7. Hermes investigator (Phase 07)

- [ ] Confirm detected Hermes identity, version, documented capabilities, and unsupported tools; disabled Hermes must not block planner/manual work.
- [ ] Run the same investigator definition in projects A and B with different canary notes; verify isolated homes, memory, files, and outputs.
- [ ] Reproduce a known fixture bug and require symptom, environment/commit, evidence, reproduction, expected/observed result, confidence, suspected causes, and missing information.
- [ ] Verify a failed/ambiguous reproduction is reported as unconfirmed, not a pass; approved patch runs use a separate worktree and sandbox.
- [ ] Kill/restart the worker during external Hermes work; reconcile the stored job ID before retry to prevent duplicate work.
- [ ] Cancel and verify actual process/service termination; verify deadlines, aggregate budget, and production-secret isolation.

## 8. Multi-agent workroom (Phase 08)

- [ ] Verify role assignments and immutable definition versions for Planner, Architect, Investigator, Implementer, Reviewer, and QA.
- [ ] Run the bug-fixture handoff: investigator evidence → patch → reviewer rejects incomplete fix → one repair → QA evidence → human review bundle.
- [ ] Confirm collaboration messages have type, sender/recipient, task/run/evidence links and do not themselves authorize tool use.
- [ ] Exhaust aggregate budget/concurrency/deadline and verify all branches stop, including retries/reviewer/subagent work.
- [ ] Simulate two tasks modifying one file; require conflict review and preserve uncommitted human changes.
- [ ] Verify reviewer independence, approvals bound to exact diff/base commit/budget/expiry, and stale approvals disabled.
- [ ] Test pause, scoped guidance, revision request, cancellation, and accurate “stopping/may still be running” states.
- [ ] Confirm project B cannot see project A messages/artifacts and task completion requires accepted review/evidence.

## 9. Client project portal (Phase 09; pilot only after migration/flag review)

- [ ] Apply the reviewed additive migration in a disposable/staging DB and verify disabled-flag behavior preserves the legacy portal.
- [ ] Preview client DTOs as an admin; publish a selected revision, make a later draft, unshare, and verify revocation of subsequent reads.
- [ ] Confirm client A sees only its authorized project/shared revisions; client B and internal projects stay hidden from lists, counts, search, events, exports, and guessed IDs.
- [ ] Verify shared tasks, milestones, notes, diagrams, progress, comments/change requests, and owner/member permission differences.
- [ ] Confirm internal notes, prompts, model output, tool arguments, private paths, terminal logs, budgets, credentials, and raw agent streams never enter the client projection.
- [ ] Test content review for AI summaries, removal of shared revisions, and warning that already downloaded files cannot be recalled.
- [ ] Confirm client requests cannot select another tenant/project or initiate billable execution without configured owner/admin approval and limits.

## 10. Operations and production hardening (Phase 10; staging before production)

- [ ] Verify liveness is process-only; readiness checks required DB/checkpoint dependencies; optional worker/Hermes/renderer failures show degraded feature states.
- [ ] Verify queue/worker heartbeat, oldest queue age, run/request correlation, redacted logs, and alerts without prompts/secrets/tenant content.
- [ ] Restore app DB, checkpoint DB, and artifact files into disposable targets; validate ownership, revisions, approvals, action receipts, audit history, and artifact hashes.
- [ ] Kill worker during mutation; exercise lease expiry/takeover, stale fencing rejection, duplicate enqueue, replay, and ambiguous external outcome reconciliation.
- [ ] Simulate app/checkpoint DB outage, model throttling, Hermes/runner loss, full artifact volume, unavailable renderer, stale approval, and session expiry during SSE/WS.
- [ ] Verify HTTPS cookies, Origin enforcement, private service ports, actual staging ingress SSE/WS timeouts, and measured concurrency limits.
- [ ] Confirm retention jobs and active-run exclusions; audit records remain immutable; restore does not repeat completed side effects.
- [ ] Exercise feature-flag rollback and prior-image recovery; document migration state, rollback target, on-call owner, backup ID, and promotion evidence.
- [ ] Promote in order: internal admin projects → client-linked admin project → one sandbox client → wider rollout; leave write/deploy capabilities off until separately approved and verified.

## Existing CSA regression checks

- [ ] Verify login/session revocation, CSRF/origin checks, project/channel management, chat send/poll, file ingest/delete, voice dispatch/transcription, FAQ portal, agent toggles/health, execution telemetry, and external channel routing still work.
- [ ] Confirm PM migrations do not alter n8n routing or customer-service provider behavior unless separately approved through the migration track.

## Sign-off

- Environment/build: 
- Feature flags/migrations: 
- Tester/date: 
- Passed / failed / not run counts: 
- Defects and evidence links: 
- Rollback target and owner: 
