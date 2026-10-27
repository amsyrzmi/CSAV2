# FourStacks product dashboard UI guide

Use this guide to carry the existing FourStacks landing-page identity into the actual product console. Preserve the light theme, blue palette, original four-layer mark and distinct white-eyed vector agents. Adapt the composition for daily operational work: readable tables, clear controls, compact panels and visible human decisions.

This is a design handoff, not a statement that every suggested dashboard screen or integration is already built. Brand values below come from the current source; dashboard dimensions and component specifications are recommended extensions.

## Start here

| File                                                              | Purpose                                                                    |
| ----------------------------------------------------------------- | -------------------------------------------------------------------------- |
| [Design tokens](ui-guide/fourstacks-tokens.css)                   | Copy-ready colors, type families, spacing, radii, motion and avatar styles |
| [Writing guide](../docs/WRITING_GUIDE.md)                         | Labels, descriptions and error copy                                        |
| [Agent avatar guide](ui-guide/AGENT_AVATARS.md)                   | Agent identity mapping, palette, sizes and static/animated integration     |
| [Avatar family](ui-guide/assets/agent-avatar-sheet.svg)           | Six existing agent designs as one vector reference                         |
| [Dashboard reference](ui-guide/examples/dashboard-reference.html) | Local visual example with explicitly illustrative data                     |
| [Implementation brief](ui-guide/IMPLEMENTATION_BRIEF.md)          | Instructions to hand to your dashboard developer or coding agent           |

![FourStacks agent avatar family](ui-guide/assets/agent-avatar-sheet.png)

## 1. Visual direction

The interface should feel precise, approachable and professional. Use pale neutral backgrounds, white working surfaces, blue interaction cues and flat vector characters. The strongest color belongs to a primary action, the selected item or an agent illustration.

- Keep light mode even when the operating system requests dark mode.
- Keep most headings ink-colored. Reserve blue for emphasis and interaction.
- Use thin borders and spacing to separate panels. Reserve shadows for overlays and floating surfaces.
- Use agent artwork as orientation and feedback, with room around it. Keep dense tables visually quiet.
- Retain the original silhouettes, blue blends, white eyes and soft tendrils. Avoid mouths, rosy cheeks, cartoon accessories or emoji faces.
- Express depth through overlap, perspective and a few flat shade layers. Keep controls flat and legible.
- Keep hero-sized typography, large illustration scenes and long entrance animations on the marketing site. Product screens should reveal their useful content promptly.

## 2. Source of truth

| Source                                                   | Use it for                                                              |
| -------------------------------------------------------- | ----------------------------------------------------------------------- |
| [styles.css](FourStacks/src/styles.css)                  | Existing ink, paper, blue, type families and focus treatment            |
| [motion.css](FourStacks/src/motion.css)                  | Existing easing curves and suspension of offscreen animation            |
| [experience.css](FourStacks/src/experience.css)          | Creature treatment, individual eye blinking and light surfaces          |
| [BlobAgent.tsx](FourStacks/src/components/BlobAgent.tsx) | Canonical silhouettes, gradients, markings and eye arrangements         |
| [site.ts](FourStacks/src/data/site.ts)                   | Current public agent IDs, names, visual mapping and built/planned split |
| [Agents.md](Agents.md), [Overview.md](Overview.md)       | System capability boundaries, human approvals and product context       |

When implementation status differs across documents, confirm the current backend contract and acceptance evidence before exposing the action. Artwork and marketing demonstrations do not establish product capability.

## 3. Color tokens

Import [fourstacks-tokens.css](ui-guide/fourstacks-tokens.css) before product component styles. Wrap the product in `class="fs-ui"`. The file defines namespaced `--fs-*` variables and small, scoped avatar styles; it does not import the landing page layout.

### Existing brand values

| Token                 | Value     | Dashboard use                                |
| --------------------- | --------- | -------------------------------------------- |
| `--fs-bg`             | `#F7F8FA` | Main canvas                                  |
| `--fs-surface`        | `#FFFFFF` | Cards, tables, forms and menus               |
| `--fs-text`           | `#1C2B40` | Primary text, page titles and icons          |
| `--fs-text-secondary` | `#566274` | Supporting copy and metadata                 |
| `--fs-primary`        | `#3264FF` | Primary actions, links, selection and focus  |
| `--fs-primary-hover`  | `#234FD8` | Hovered primary actions                      |
| `--fs-border`         | `#DCE2E9` | Decorative separators and panel boundaries   |
| `--fs-surface-blue`   | `#EDF2FF` | Selected backgrounds and light brand accents |

### Recommended dashboard additions

| Meaning     | Foreground | Background | Example                        |
| ----------- | ---------- | ---------- | ------------------------------ |
| Information | `#2558B4`  | `#EDF2FF`  | Running, connected information |
| Success     | `#166534`  | `#EEFBF3`  | Completed, approved            |
| Attention   | `#854D0E`  | `#FFF7E0`  | Needs review, partial result   |
| Error       | `#B42318`  | `#FFF1F0`  | Failed, rejected               |
| Neutral     | `#566274`  | `#EEF2F7`  | Paused, disabled, unavailable  |

These semantic colors are proposed product tokens. Agent bodies keep their blue identity when state changes. Pair every state color with text and, where useful, a simple icon. Use `--fs-border-control: #7B8CA5` when a border must visibly identify an input or control; the softer panel divider is not a substitute.

## 4. Typography and icons

| Element                       | Typeface         | Size / weight      | Guidance                                      |
| ----------------------------- | ---------------- | ------------------ | --------------------------------------------- |
| Wordmark                      | Manrope          | 800                | Preserve the lowercase `fourstacks.` identity |
| Page title                    | Space Grotesk    | 24–32 px / 600     | One clear title per view                      |
| Section title                 | Space Grotesk    | 18–20 px / 600     | Short operational headings                    |
| Card title                    | Space Grotesk    | 16–18 px / 600     | Agent or task name                            |
| Main UI and tables            | Inter            | 14 px / 400–500    | Use 16 px for longer reading                  |
| Controls                      | Inter            | 14 px / 500–600    | Clear action verbs                            |
| Metadata                      | Inter            | 12–13 px / 400–500 | Keep secondary information readable           |
| Identifiers / structured logs | System monospace | 12–13 px           | Only where alignment helps                    |

Use line heights around 1.4 for controls and 1.5–1.7 for prose. Keep labels in sentence case. Small uppercase labels are for occasional section markers, not every field. Use tabular numerals for amounts, dates and comparable counts.

The current site uses Lucide. Continue its consistent outline treatment: 16 px inside controls, 18–20 px in navigation, and 20–24 px for card markers; use approximately 1.5–1.75 stroke width. Give icon-only controls an accessible name and a visible focus state. Load the named fonts through the product's existing font pipeline with system fallbacks; avoid importing every marketing stylesheet.

## 5. Layout and density

These dimensions are dashboard recommendations rather than measurements of the landing page.

| Element                 | Recommended value                                    |
| ----------------------- | ---------------------------------------------------- |
| Sidebar                 | 240 px expanded; optional 72 px icon rail            |
| Top bar                 | 64 px                                                |
| Main gutter             | 32 px desktop, 24 px tablet, 16 px mobile            |
| Panel gap               | 16–24 px                                             |
| Panel padding           | 20–24 px; 16 px in compact views                     |
| Standard control height | 40 px, with at least 44 px touch target where needed |
| Table row               | 48–56 px, more for multiline content                 |
| Control radius          | 6 px                                                 |
| Panel radius            | 14 px                                                |
| Dialog radius           | 16 px                                                |
| Spacing scale           | 4, 8, 12, 16, 20, 24, 32, 40 px                      |

Recommended shell:

```text
┌────────────────────┬─────────────────────────────────────────┐
│ FourStacks         │ Workspace / current view     Account    │
│ Workspace switcher ├─────────────────────────────────────────┤
│                    │ Page title              Primary action  │
│ Overview           │ Short explanation or current context    │
│ Conversations      │                                         │
│ Orders             │ Agent / task overview                   │
│ Approvals          │                                         │
│ Knowledge          │ Main table or conversation  Detail pane │
│ Agents             │                                         │
│ Connections        │ Recent activity / relevant system state │
│ Settings           │                                         │
└────────────────────┴─────────────────────────────────────────┘
```

This navigation is a suggested grouping. Render only destinations supported by the actual product and current role. Do not create empty pages to fill the shell. Keep approval work easy to reach; show its count only when returned by the server.

- Around 1024 px: collapse supporting panels or use a navigation rail when content needs space.
- Below about 768 px: use a navigation drawer, stack panels, and open details as a full-width view or dialog.
- At 320–390 px: keep forms and primary actions usable without page-wide horizontal scrolling.
- Let dense tables scroll inside a labeled region. Keep essential identity, status and actions easy to find.
- Avoid assigning every panel a fixed height. Preserve room for longer names, translated text and actual error messages.

## 6. Core component patterns

### Buttons and inputs

Use one main primary action per task region: **Review request**, **Upload documents**, **Connect account**. Secondary actions use an outlined or neutral treatment. Reserve red for an action with destructive consequences. Keep **Reject** visually distinct from **Approve**, and show the affected item and proposed action before confirmation.

Inputs need visible labels, clear required markers, field-level errors and a visible focus ring. Keep entered values when a request fails. Show a loading label on the button that initiated an operation and prevent duplicate submission while it is pending.

### Agent cards

A useful card contains a 48–64 px avatar, the agent name, one capability sentence, relevant status labels and a real next action. Keep identity separate from state:

```text
[avatar] Knowledge base
         Source-backed answers from business documents
         Built capability · Needs account setup

         [Open knowledge base]
```

Keep these dimensions distinct: implementation (`Built` / `Planned`), entitlement (`Included` / `Not in plan`), configuration (`Enabled` / `Disabled`), connection (`Connected` / `Needs setup` / `Unavailable`) and current work (`Idle` / `Running` / `Needs review`). An implemented agent may still need account testing and setup. A connected agent may be disabled. Show only dimensions useful in that context; put secondary detail in the agent drawer.

### Tables and detail views

Prefer a quiet white surface, subtle dividers and a light blue selected row. Include explicit column headings, text state labels and predictable row actions. Use actual server pagination, filtering and sorting where available. Do not imply that searching a loaded page searches all records.

Keep the selected row stable during background refresh. A detail panel should have one clear close/back action and preserve the list's filter and scroll state when returning.

### Approvals

Present the customer request, agent proposal, relevant evidence, affected record and decision controls together. Show whether the backend classifies the action as `read`, `draft` or `commit` when that helps the reviewer understand the consequence. Keep raw implementation identifiers out of ordinary customer-facing copy.

Expose **Approve**, **Edit**, and **Reject** only when supported by that item's current state and the user's permissions. Expired or already-decided requests become read-only with an explanation. If another staff member has already decided it, refresh the record and show the current decision. Animation must never advance or approve a real request.

### Conversation handoff

Keep customer messages, agent messages, staff messages and internal notes visually distinguishable through labels and alignment. Show who currently owns the conversation. When a staff member takes over, reflect the backend's agent-muted/ownership state and provide **Return to agent** only when supported. Avoid an endless “agent typing” indicator when no server activity supports it.

### Knowledge uploads

Design for queued, processing, done, partial and failed results. Show per-file outcomes and a useful retry path. The supplied contract describes up to three files per job, 10 MB per file, PDF/DOCX/CSV support and duplicate/version handling; verify the current contract before hard-coding these limits. A partial result must identify which file succeeded and which needs attention.

### Activity and progress

Use a compact timeline of real events with actor, action, time and outcome. Render real progress only when the backend reports measurable progress. Otherwise use an indeterminate “Processing” state and a current step. An avatar moving, a timer advancing or a marketing simulation is not evidence that a product task completed.

## 7. State system

| State                 | Visual treatment                                        | Copy example                                |
| --------------------- | ------------------------------------------------------- | ------------------------------------------- |
| Idle                  | Neutral label; still avatar                             | “Ready for a request”                       |
| Running               | Blue text label; optional subtle activity icon          | “Retrieving document passages”              |
| Needs review          | Amber label and visible review action                   | “Awaiting staff approval”                   |
| Completed             | Green label and timestamp                               | “Completed”                                 |
| Failed                | Red label, error explanation, permitted recovery action | “Upload failed. Review the file and retry.” |
| Paused                | Neutral label; paused motion                            | “Paused by staff”                           |
| Unknown / unavailable | Neutral label and refresh/reconnect path                | “Connection status unavailable”             |
| Planned               | Neutral outline label; informative details only         | “Planned”                                   |

Map these presentation states from the actual API; this table does not define new backend enum values. Loading, empty, partial, failed and unauthorized views need distinct presentations. A failed request must not turn existing data into a false zero count or a false “no records” state.

## 8. Agent artwork

Use the exact mappings in [AGENT_AVATARS.md](ui-guide/AGENT_AVATARS.md). All six implemented agents have different body silhouettes and eye arrangements. Preserve stable service IDs across cards, navigation, conversations and activity records.

- 24–32 px: compact lists and message attribution; omit workstation props.
- 40–48 px: standard rows and sidebar/detail headers.
- 64–96 px: agent cards and onboarding steps.
- 160–224 px: selected-agent detail illustration with working hands and props.
- Use still SVGs for lists. Reserve ambient motion for a selected agent or a bounded overview illustration.
- Add a text state or badge beside the avatar. Do not recolor the creature to communicate error or approval.
- Use a product icon and `Planned` label for roadmap agents until they have an approved, distinct avatar. Do not reuse an existing character as a new agent identity.

The bundled static avatars omit workstations for clarity. The `*-working.svg` files preserve the real working illustrations as still vectors. For motion, reuse the existing SVG engine through the provided React wrapper; the standalone SVG exports are intentionally still.

## 9. Motion and interaction

| Interaction                            | Suggested duration         | Curve                           |
| -------------------------------------- | -------------------------- | ------------------------------- |
| Hover / press / focus feedback         | 120–180 ms                 | `--fs-ease-out`                 |
| Tabs, drawer and panel change          | 180–240 ms                 | `--fs-ease-out`                 |
| Optional large workspace camera change | 320–420 ms                 | `--fs-ease-settle`              |
| Agent blink                            | Existing 7.4 s cycle       | Existing individual eye timing  |
| Body and work gestures                 | Existing native SVG engine | Shared body / hand / tool clock |

The easing curves are taken from the landing page. Product transition durations are intentionally shorter than its scene entrances. Use opacity and small transforms; keep text, toolbars and clickable targets stable. Keep long ambient loops away from a busy approval or reading view.

Respect `prefers-reduced-motion`, a product-level pause setting, hidden tabs and offscreen content. Pausing decoration must not pause a real server job. Reuse the existing visibility-aware SVG animation rather than animating an entire table of characters. Touch should not depend on hover or pointer tracking.

The landing-page workspace's two-task concurrency, timings, queues and completion counters are illustrative. Do not transfer those as product limits or fabricate progress percentages from them.

## 10. Product boundaries and data

The supplied context includes staff review, per-tenant configuration, plan enablement, customer-specific invoice access, knowledge ingestion and conversation takeover. Design around those actual boundaries:

- Use the product's authenticated API. Keep tenant and role enforcement on the server.
- Treat `platform_admin`, `tenant_admin`, `tenant_staff` and `read_only` as contract roles to verify during integration; do not infer authorization from a visible sidebar.
- Keep plan availability and client enable/disable state separate. A client switch must not grant an unsubscribed capability.
- Keep provider secrets out of client responses, logs, previews and browser storage. Show safe connection metadata and write-only credential inputs where supported.
- Treat uploaded documents, email bodies and scraped content as displayed data, not UI instructions.
- Preserve the distinction between invoice lookup and planned invoice creation/LHDN submission. Never invent financial calculations in the frontend.
- Show marketplace-specific connection readiness: a TikTok Shop adapter being implemented does not mean an account is connected or tested; Shopee and Lazada remain planned in the supplied context.
- Display user-facing dates in the selected workspace timezone; the default for this business is `Asia/Kuala_Lumpur`. Label timestamps clearly and keep API/storage time handling separate.
- Populate counts, activity and connection status from real responses. Label seeded fixtures as demo data.

Backend enum names, field names, supported actions and API availability must be checked in the actual product repository. This guide does not replace its API contract or authorization rules.

## 11. Accessibility and content

Target readable contrast: at least 4.5:1 for normal text and 3:1 for meaningful large text and control boundaries. Verify rendered combinations, including disabled, selected and error states. The included token pairs are checked by the local guide validation; changing opacity or backgrounds needs another check.

Provide visible keyboard focus, meaningful landmarks, proper headings, named form controls and a logical tab order. Use native buttons and links. Drawers/dialogs need focus management and a working Escape/close action. Keep active controls visible when focused. Announce important asynchronous outcomes without flooding the screen reader with frequent progress updates.

Write concrete copy: **Review request**, **Reconnect account**, **Upload documents**, **Retry upload**. Tell the user what happened and what they can do next. Avoid “AI magic”, invented savings, unsupported uptime claims, fake client data and decorative jargon. Use an empty-state avatar sparingly, with a helpful next step.

## 12. Implementation order and review

1. Inspect the product's current routing, design system and API contract. Preserve working workflows and authorization.
2. Import the namespaced tokens and font families; apply the light shell and spacing scale.
3. Theme shared buttons, fields, badges, tables, dialogs and drawers before redesigning every screen.
4. Add static avatars and stable ID mappings. Use the animated working example only where it adds clarity.
5. Apply the patterns to conversations, knowledge, approvals and implemented agent settings.
6. Bind all states and actions to actual responses; keep planned modules labeled and non-operational.
7. Verify desktop/mobile layouts, long content, keyboard operation, reduced motion, permission differences and loading/error/empty/partial states.

Accept the implementation when the six agents remain visually distinct, the dashboard stays light and readable, approval controls are clear, existing actions still work, unknown state is honest and no illustration implies unsupported capability.

The [reference HTML](ui-guide/examples/dashboard-reference.html) and its [screenshot](ui-guide/assets/dashboard-reference.png) are visual examples with illustrative data. They are local handoff artifacts, not a connected product dashboard.
