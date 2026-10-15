# Phase 09 — Client project workspace with explicit sharing

Prerequisites: phases 02–05 and all scope/visibility tests. Hermes and terminal can remain admin-only; clients do not inherit infrastructure access when PM is enabled.

## IDE AI work

1. Extend the existing `/portal/projects` and `/portal/projects/[id]` pages. Reuse common project/task/note/artifact services behind client-specific DTOs and operation policies.
2. Infer client identity from CurrentUser. Add optional project memberships for within-tenant access: owner/admin manages access, members see explicitly assigned projects unless existing product rules intentionally grant tenant-wide access. Document the choice and migrate existing permissions compatibly.
3. Provide milestones, selected tasks, shared notes/diagrams, a progress summary and comments/change requests. Decide task editing permissions explicitly; start with owners reviewing plans/acceptance and members commenting rather than granting unrestricted changes by accident.
4. Sharing publishes a selected revision with a dedicated sanitized client projection. Removing visibility revokes subsequent access; new edits stay draft until shared. Warn that files already downloaded cannot be recalled. Internal projects cannot become client-visible by artifact flags.
5. Build client progress events from authorized public records, not a filtered raw agent stream. Model output, file paths, internal notes, prompts, tool arguments and terminal logs are admin-only by default. Run an explicit content review for client-facing AI summaries; metadata visibility alone does not sanitize text.
6. Keep global agent editing, repository credentials, budget policies, execution approvals and terminal creation admin-only. A future client AI action uses a narrow client-safe tool policy rather than admin permissions.
7. Client requests that trigger billable/project-changing work require the configured owner/admin workflow, rate limits and budget. No hidden client body field can choose another project/tenant or publish technical artifacts.
8. Add a “Preview as client” view for admins using client DTOs; do not impersonate by weakening backend guards.

## Acceptance

Client A sees only selected revisions of its permitted projects; Client B and internal records stay inaccessible through search, counts, events, export and guessed IDs. An internal note referenced by a shared artifact does not become downloadable. Sharing/unsharing and new revisions behave correctly. Owner/member permission differences are tested. Disabled client-PM flag returns the legacy portal behavior while admin PM remains available.

## Operator work

Pilot one sandbox client and review the actual client view before wider enablement. Client terminal and cloud execution are outside this phase.
