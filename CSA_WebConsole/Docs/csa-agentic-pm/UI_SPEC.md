# Project workspace UI specification

## Existing design to preserve

Use the current CSA icy-blue canvas `#F4F7FE`, blue accent `#2563EB`, navy text, Inter, JetBrains Mono, glass cards, AppShell, command palette and kit primitives. Prefer calm, useful interactions; complex agent activity should be legible at a glance.

## Information architecture

Admin sidebar: Projects, existing Agents monitoring, new Project agents, existing System and Settings. `/projects` is a searchable card/table switcher with client/internal badges, lifecycle filter, attention status and next milestone. Creation lets an admin select “Client project” and a real client, or “Internal project”. Selecting an existing client project opens that exact ID.

Project header: project name, client/internal badge, lifecycle, permission/visibility badge, repository connection, workspace health and primary action. Navigation: Overview, Board, Notes, Diagrams, Architecture, AI workroom, Files; Terminal is available only to permitted admins when its runner is configured.

## Workspace panels

| View | Primary interaction | AI assistance |
|---|---|---|
| Overview | Goals, next milestone, risks, activity and accepted progress | Generate/revise plan; identify blockers |
| Board | Drag cards across Backlog/Ready/In progress/Review/Done; blocked badge | Propose breakdown and dependencies |
| Notes | Folder tree, Markdown editor/preview, backlinks and graph | Summarize selected notes or draft ADR |
| Diagrams | Source editor, preview, type catalog, versions | Generate/update with evidence |
| Architecture | Editable nodes, boundaries, inspector, assumptions | Propose app/AWS design and tradeoffs |
| AI workroom | Run timeline, assigned roles, tool cards, approvals | Observe bounded work, intervene or cancel |
| Terminal | Resizable project tabs, owner and workspace badge | Open the run's logs or sandbox checkout |
| Files | Authorized project artifacts and export history | Cite selected evidence in a task |

Persist splitter positions and view preferences per user/project; never put sensitive content or auth tokens into localStorage. Support keyboard resize, focus management, reduced motion, and mobile views that stack panels. Lazy-load diagram/canvas/terminal modules in client components to avoid server rendering issues and unnecessary bundle growth.

## Task drawer

Title, description, priority, human owner, selected agent role, milestone, labels, estimates marked as estimates, dependency list, acceptance criteria, comments, evidence and linked note/diagram/run. Card version conflicts show “This task changed” with refreshed values; do not silently overwrite another user's edits. Optimistic drag updates roll back on API failure. Do not calculate project completion solely from a count of generated tasks; count accepted outcomes or completed tasks with a defined scope.

## AI composer and proposals

Entry actions: Plan this project, Investigate a bug, Review selected files, Generate diagram, Suggest architecture, Implement task. Show context selection, repository commit, tool permissions, budget and agent role before starting. Project ID is route context, validated by server; it is not free-form agent text.

The plan result is a reviewable proposal with milestones, risks, task acceptance criteria and assumptions. Buttons: Accept selected, Edit, Reject. Acceptance creates real tasks atomically once. AI output never quietly replaces the existing board.

## Workroom and agent collaboration

Show role avatars, current assignment, progress and a human-readable timeline. Each collaboration message has type, sender, intended recipient, task, source links and an explicit summary. Clicking a run opens bounded tool inputs/results, changed files, tests and cost. Keep secrets and raw production logs out of this view. Display blocked/offline workers as actual states, not simulated typing indicators.

Approval cards show exact action, target, diff/commands, base commit, budget, expiry and consequences. Buttons: Approve this action, Request revision, Reject. If the base commit changes, disable approval until revalidation. Cancellation shows “Stopping” until the worker confirms termination; retain “May still be running” if a remote service cannot confirm it.

## Notes graph and architecture canvas

Backlinks connect stable note IDs; titles can change without losing links. The graph highlights the selected note and its immediate neighbors rather than rendering a giant unfiltered graph. A double-click opens a note. Keyboard/list alternatives expose the same relationships.

Architecture supports accounts/regions/VPC/AZ groups, typed component nodes, semantic edges, search, zoom to fit, minimap and an inspector. A generated graph is editable. Source diagrams and editable graphs have distinct formats; avoid promising lossless round-tripping between arbitrary PlantUML/Mermaid and canvas coordinates. Store layout changes separately from semantic version changes when useful.

## Failure and privacy states

Every view has loading, empty, unauthorized, unavailable and error states. AI unavailable must not disable manual project work. Terminal must state where the shell actually runs. Client pages later hide internal-only agent discussions, terminal controls, private repository paths and budget/model administration. Badge shared revisions explicitly; preview the client view before publishing.

## Manual acceptance demo

At desktop and narrow viewport: create internal project → create five tasks → keyboard move one task → link a note → generate/edit a supported diagram → accept an AI plan proposal → inspect a run → switch projects and verify panel preferences/content are correctly scoped. Test focus return from drawers, error banners and command palette actions.
