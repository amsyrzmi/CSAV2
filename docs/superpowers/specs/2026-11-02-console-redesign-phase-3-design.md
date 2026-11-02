# Console redesign phase 3: calmer screens for non-technical people

Date: 2026-11-02. Same branch as phases 2 and 2b (`feat/console-redesign-phase-2`, unpushed), which now also contains all of `origin/main` up to `cb13b35` (merge `03a92c5`). Specs `2026-10-29-console-redesign-phase-2-design.md` and `2026-10-30-console-redesign-phase-2b-design.md` stay binding for everything not changed here. Facts behind every statement below are in `docs/superpowers/specs/2026-11-02-console-redesign-phase-3-facts/` (four fact sheets read from the code on 2026-11-02; quoted file:line references live there).

## 1. Intent

The owner reviewed phase 2b and asked for a calmer, simpler console that a shop owner or office worker with no software background can use without training. The request, in the owner's order:

1. The avatar animation must suit every theme (dark included) and must not sit in a box.
2. Remove the long "What it cannot do" text from the Overview chat.
3. Make the chat simpler and more interactive.
4. Add an avatar for e-invoicing.
5. Ideas for simplifying every People & HR tab (delivered as a document; owner approved building ideas 2 to 5, not idea 1).
6. Assistant activity: less technical; technical details behind a down arrow; five recorded runs at a time; a small animated view of the last five assistants used and how their finished runs ended; a calendar-style date filter.
7. Knowledge base: simpler, better layout; a better name was suggested (see 9, deferred).
8. Customer inbox: already acceptable; make it friendlier.
9. Email assistant: friendlier for non-technical staff.
10. Business management: far simpler; details behind a down arrow; filters with a calendar-style date range and relevant statuses.
11. Subscriptions: improve the experience.
12. Team: simpler, friendlier layout.
13. Password: improve the styling.
14. Scan what other developers added, and bring the new features into the same look.

The why: the console's value is that a small business can run an AI customer team without a technical person. Today several screens show internal words, milliseconds, ids and long forms first. Phase 3 puts the plain meaning first and the detail one click away.

## 2. Scope

In this round, by phase (the owner previews after each):

- **Phase A, shared pieces and quick fixes.** The four shared building blocks (section 6.1); avatars without a tile and readable on all themes (6.2); the e-invoicing avatar (6.3); the Password page polish (6.4).
- **Phase B, chat.** Remove the long limits text and expander; simpler, more interactive chat (6.5).
- **Phase C, the tabs.** Assistant activity (6.6), Knowledge base (6.7), Business management (6.8), Customer inbox (6.9), Email assistant (6.10), Subscriptions (6.11), Team (6.12).
- **Phase D, People & HR and the other new features.** Owner-approved ideas 2 to 5 (6.13) and restyling the newly merged screens onto the shell tokens and shared pieces (6.15).

## 3. Non-goals and anti-goals

- **No wording rewrite of existing text.** The owner said "do not fix any labelling yet". Existing visible strings, nav names and statuses stay exactly as they are, including the name "Knowledge base" and People & HR idea 1 (the plain-word pass over Leave, Requests, Training, Recruitment, Payroll, Settings). New elements (an arrow, a chip row, a calendar, a get-started card, a summary line) carry new plain text, written under `docs/WRITING_GUIDE.md`. A later labelling round, with a vocabulary table, follows. Exception: strings that belong to the owner-approved HR ideas 2 to 5 (6.13).
- No new product capabilities. Business management does not gain invoices, bookings or customers tabs. The assistant gains no new abilities, and no copy may promise more than it does.
- No behaviour change to irreversible actions except where 6.10 and 6.12 say so (extra confirmation only).
- No change to access rules, roles, sessions or what the server returns by default. Backend additions are optional query parameters and one small read, with defaults identical to today (section 7).
- No colour literals outside the token files; no animation that stands for state (a badge and words always carry the state).
- Do not rewrite `csa/hr`, `csa/email` or `csa/workspace` logic. Do not restructure the other developer's HR screens, only restyle and apply ideas 2 to 5.
- No push, PR or merge in this round unless the owner asks.

## 4. Constraints

All phase 2 and 2b constraints stay: stable `data-testid`s; five widths (320, 390, 768, 1024, 1440) without overflow; three themes at the contrast ratios; keyboard and focus; reduced motion and the pause preference; tokens only (`tests/theme-literals.test.mjs`); Next.js docs read before touching layouts; one web check per run on the shared host and CI as the merge gate; new `lib/` and test files go into `scripts/check-isolated.mjs` (staged list and commands list); the default `proxy.ts` allowlist stays deny-by-default. `SandboxWorkspace` keeps its name and props because the voice and sandbox browser harnesses mount it directly. `AgentCard` stays exported from `RuntimeWorkspace.tsx` (Services imports it). Where existing tests assert old wording on screens that are restyled, the wording stays so the tests keep passing; where a test pins structure (for example `identity-transport.test.mjs` reading `BusinessWorkspace.tsx` source), update the test and list the change.

## 5. Approaches (owner picked A on 2026-11-02)

- **A. Shared building blocks first, then every tab uses them.** One expandable-details row, one calendar date-range filter, one status-chip row and one short page intro, built and tested once. Consistent screens, one date picker, one place to fix accessibility. Slower to the first redesigned tab.
- B. Each tab on its own: faster first screen, three date pickers that drift apart.
- C. One round and review per tab group: smallest reviews, slowest, and half-redesigned navigation in between.

## 6. Design

### 6.1 Shared pieces (`components/console/kit/`)

- **`DetailsRow`.** A row with a plain summary line and a chevron button (`aria-expanded`, `aria-controls`); the technical details region opens under the summary. Whole summary line is not the toggle (links and buttons inside stay clickable); the chevron and a "Show details / Hide details" text are. Keyboard: Enter/Space on the button. Reduced motion: no height animation.
- **`DateRangeFilter`.** A button showing the chosen range ("Any date" by default) that opens a calendar popover: a month grid with previous/next month, click start then end, a highlighted range, today marked, and preset buttons (Today, Last 7 days, Last 30 days, This month, Clear). Built in-house (no new dependency), keyboard operable (arrow keys move the focused day, Enter selects, Escape closes and returns focus), screen-reader labelled grid, full-width sheet below 640 px. Value is two local calendar dates; the caller converts them to start-of-day and end-of-day instants in the browser's time zone for the server. The component shows no times.
- **`StatusChips`.** A row of toggle chips (single or multi select, set by prop) with optional counts, an "All" chip, wrapping on narrow screens; selected state is shown by text weight and a check mark, not colour alone.
- **`PageIntro`.** A one- or two-line sentence under the page title saying what the page is for, from a prop.
- All four use only theme tokens, are exported from `components/console/kit/index.ts` and have node tests for their pure logic (range selection, month grid, preset ranges, chip state).

### 6.2 Avatars without a box, on every theme

- `AgentAvatar` stops drawing the rounded tile: no background, no `overflow: hidden`, no border radius. The sprite is drawn straight onto the surface. The second chip around the avatar in the Services row (`CHIP` in `ServicesWorkspace.tsx`) and in `RuntimeWorkspace.tsx` goes too.
- **Readability on the night theme** (the recon computed that the darkest tones of support, hr and assistant drop below 3:1 against the night surface once the tile is gone). Solution, my call: a one-pixel sticker outline in a theme token, `--fs-sprite-halo`, drawn with four crisp `drop-shadow` offsets of one grid cell. Night: a light halo (the former tile colour); cobalt and bureau: transparent (the sprite already contrasts there). The sprite art and the generator stay untouched.
- Tests: replace the tile assertions (`pixel-avatars.test.mjs`, `theme-contrast.test.mjs`) with: every avatar's silhouette edge cells reach 3:1 against each theme's surface counting the halo; the halo token exists in all three themes. A browser check that no avatar element has a background or radius.
- Rejected: a night-only palette (doubles the data and diverges from the generated source), a CSS brightness filter (shifts hues unpredictably).

### 6.3 E-invoicing avatar

- A twelfth avatar, id `einvoicing` (matches `[a-z]+`), for the capability `lhdn_agent` and the package `e_invoicing` (title "E-invoicing (LHDN MyInvois)"; the package is preparation only, so the avatar is used as a service and agent icon, nothing implies submission).
- Character: same pixel family as the other eleven, carrying a document-and-stamp motif (a receipt with a seal) so shape, not hue alone, makes it recognisable. Hue from the largest free arc (about 120, a green; builder confirms against the HUE table and the 21 to 25 degree neighbour gap). Idle motion and an eight-frame working loop (stamping) follow the existing helpers.
- Touches (from the recon): `AGENT_IDS`, titles, capability and package maps in `lib/agent-avatars.ts`; the source SVG in `public/fourstacks/agents` and the ui-guide; `HUE`, `idle_frames`, `PROPS` and `working_plan` in `scripts/pixelate_avatars.py` (the generator's Python half runs without a browser, so the builder feeds a hand-drawn 32 and 48 grid; the Chromium `--check` is proven later by hand); `lib/pixel-avatars-data.ts`; tests (`agent-avatars`, `public-assets`, the HUE table in `pixel-avatars-data.test.mjs`, and flip the two browser tests that currently assert E-invoicing has no avatar). It must pass the same 3:1 edge rule on night with the halo.

### 6.4 Password page

Same endpoint, rules, messages and testids. Visual polish on the in-shell page: a card with the three fields; a show/hide button per field; a live checklist under the new password (at least 12 characters, differs from the current one, matches the confirmation) that ticks as the person types, using text and a check mark; a strength hint that is advisory only and never blocks (length-based, no dictionary, nothing sent anywhere); a clear success panel after saving that says other devices are signed out (the backend revokes sessions, confirmed in phase 2b). The forced `/change-password` page shares the same card.

### 6.5 Chat (Overview)

- **Remove**: the "What it cannot do" expander and its long text, the repeated limits sentences in the empty state, and the compact one-liner's expander link. (Owner: "remove this".)
- **One honesty line stays**, my call, a single quiet line under the composer: "Answers come from your knowledge base and products." Reason: the assistant must not look like it can do more than it can, and the long text is gone. The owner can ask to remove it.
- **Simpler**: the empty state is the avatar, "How can I help today?" and three starter chips (unchanged from the final review: they only prefill the composer). The voice controls collapse into one microphone button with a small menu for the two voice modes; "Stop reading aloud" appears only while speech is playing; "Check for new messages" appears only after a failed automatic refresh (the screen already polls every 2.5 s); the toolbar keeps New chat, history and archive.
- **More interactive** (all front-end): the composer grows with the text; while a reply is pending the assistant avatar shows its working pose beside a plain "Working on it" line (a pending turn is a server-confirmed state, so the animation is truthful); new messages ease in (none under reduced motion); a "Jump to latest" pill when scrolled up; each reply gets a Copy button; after a reply, up to three follow-up starters ("Tell me more", "Say it shorter", "Give an example") that only prefill the composer; Enter sends, Shift+Enter adds a line (as built).
- Preserved behaviours (do not break): held, queued and failed wording; the retained request key and uncertain-send retry; the 90-second delay notice; read-only role; recorded voice flows; the draft kept on a failed create. The voice and sandbox browser harnesses keep mounting `SandboxWorkspace`.

### 6.6 Assistant activity

- **Header and calm summary**: `PageIntro` plus one quiet line from the existing 24-hour overview ("Last 24 hours: N runs, M needed attention"; numbers only from the read, never 0 on failure).
- **Last five assistants** (new strip). Source: `GET /console/v1/runs?limit=100` read once, de-duplicated by avatar id (the recon found `ticket_agent` and `escalation_agent` share `tickets`, `order_agent` and `order_approval` share `orders`; only 9 capabilities, 7 avatars, ever appear). Up to five cards, newest first, each with the avatar, the assistant's plain title (`AGENT_TITLES`), when its latest run finished and how it ended. Fewer than five cards if fewer have run; an empty strip says nothing has run yet. Every run row in the data is finished, so there is no "working now" state to show and the strip never pretends otherwise. Motion: when the strip appears each avatar plays its short wake animation once and then rests (finished OK), while a run that ended in error shows the avatar still and dimmed with an amber "Needed attention" badge; the badge text and time carry the state, the motion is decoration, and reduced motion or the pause preference shows the rest pose only.
- **Runs list**: five rows per page with Newer and Older buttons (keyset paging: a stack of `before_id` cursors). Each row's summary line is plain: avatar, assistant title, when, and "Worked" or "Needed attention". A chevron (`DetailsRow`) opens the technical block: run number, node, status code, duration in milliseconds, error code, agent id, and the existing detail fields. The old `#<id> <agent>` button and the `Run #<id>` heading move into the opened block so the packaged-console test is updated to open the arrow first.
- **Filters** (above the list): `DateRangeFilter`, an assistant select (existing `agent` parameter), and `StatusChips` "All, Worked, Needed attention". Filtering is on the server (section 7), so counts and paging stay honest.
- Dead `AgentManagement` branch (`mode="agents"`) is removed, `AgentCard` stays exported. Colour literals none.

### 6.7 Knowledge base

- **Layout**: `PageIntro`; a prominent "Add knowledge" area with the existing upload control and three plain steps beside it (1 Add a file or write a note, 2 Your assistant reads it, 3 It answers from it); documents as cards (title, type, added when, a status chip Ready, Reading or Needs attention) with a `DetailsRow` for file size, indexing job, chunk counts and errors; the gaps list ("questions the assistant could not answer") as a to-do style list with the existing actions; the agent panel (kb agent on or off) moves into a quiet "Settings" disclosure at the bottom.
- **Reliability fixes** (front-end): the five lists load independently, so one failing call shows an inline retry only for its section instead of blanking the page; the 5-second refresh runs only while a job is in progress, then stops.
- No server filters are added (the owner did not ask for filtering here).
- The nav name "Knowledge base" and the sandbox chat copy stay. Name options for the later labelling round (not applied now): "Help library", "What the assistant knows", "Business knowledge".

### 6.8 Business management

- **Orders list** as cards: customer or channel, total in RM (not "MYR"), status chip, placed date; a `DetailsRow` per order opens items, payment status, shipping times (`placed_at`, `shipped_at`, `created_at`, `updated_at`, now all shown), channel and shop ids, and the raw status codes. Products and local store records get the same card + arrow treatment under the existing sections.
- **Filters**: `DateRangeFilter` (by placed date), `StatusChips` for order status (draft, pending_confirmation, confirmed, processing, shipped, delivered, cancelled, refunded, shown in plain forms such as "Waiting for confirmation", the codes stay in the opened details) with per-status counts, and a payment-status select (unpaid, COD pending, paid, refunded, failed). The existing channel and shop filters stay.
- **Paging**: "Older" now accumulates instead of replacing the list (front-end fix of `useRecords`).
- **Server**: see section 7 (orders gain `from`, `to`, `status`, `payment_status` and a counts read).
- Test-coupled parts that must survive: the DEMO MODE strings, the `DemoBusinessWorkspace` / `BusinessRecords` split and the two disabled "Connect unavailable" / "Reconnect unavailable" buttons asserted in `identity-transport.test.mjs`; the TikTok track will replace those buttons later.

### 6.9 Customer inbox

Already accepted by the owner; improvements are layout and interaction only, wording unchanged. Grouped filter bar (a segmented control for channel, "Needs review" and sender kind as `StatusChips`, search with a clear button), roomier conversation rows (avatar, name, last message preview, relative time, a small "needs review" marker), a clearer empty state with the next action, and a sticky composer area in the open conversation. Server filters stay as they are (channel, needs-review, known or new sender, search at 20 per page with cursors). The inbox stays free of date filtering because the server has none and a client-side date filter would cover only the 20 loaded rows. **Added confirmation**: "Queue reply" gets the same Confirm/Cancel step Approve and Reject already have (my call, it is the one inbox action that sends without a pause). The inbox tests pin exact wording, so every existing string stays.

### 6.10 Email assistant

Layout and interaction only, wording unchanged except new elements. A status bar on top (mailbox connected, last sync time, the Sync action); `StatusChips` over the loaded cache for Unread, Needs reply, Drafts and a category/priority select, plus `DateRangeFilter` over the cache, always with the honest note "Showing the last 90 days, up to 200 emails." (the server returns only the cached window, so every filter is client-side and labelled as such). Platform administrators see company names instead of raw UUIDs in the picker from data already on the client (the tenant list), falling back to the id. A tidier thread list, a reading pane with the reply box docked, and the draft editor in a side drawer. **Added confirmation** (my call): Delete draft, Disconnect Gmail and Switch off for tenant each get a Confirm/Cancel step. The send dialog, version check, idempotency key and the stuck-send release flow stay exactly. `tests/test_email_management_browser.py` pins sync and attention call counts, so the redesign must not add calls.

### 6.11 Subscriptions

Keep the three tabs (Customers, Plans, Requests) and the three-step change flow. Add: a summary row of real numbers computed from the loaded data (customers active, ending soon, requests waiting; hidden when a read fails); visible filter labels and `StatusChips` on the customer list; a short "what this does" help line (new text) beside the jargon fields ("Cancel at period end", "Special price in RM", "Short code cannot change later") without changing the field labels; friendlier empty states with one next action; the timeline shows times in the tenant time zone (it ignores it today); the drawer becomes sectioned (who, plan, price, when) with the review step summarised in plain sentences first and the raw values behind a `DetailsRow`. No backend change.

### 6.12 Team

Card-based member list (initials avatar, name, email, role pill, status), a primary "Invite someone" button opening the invite form in a drawer, invitations as a compact list with status chips and a details arrow, and the same layout for the platform administrator's Members page. **Added safety** (my call, plain facts from the recon): changing a role and Disable access each ask for a Confirm step that says the person will be signed out (the server does that today and the UI never says); "Revoke sessions" shows a confirmation message. The invite logic, access defaults, mail flags and HR-access hooks are untouched. The invite acceptance page under `app/(auth)/invite/` stays as is.

### 6.13 People & HR (owner-approved ideas 2 to 5; idea 1 is NOT built)

Source: `docs/superpowers/specs/2026-11-02-console-redesign-phase-3-facts/people-and-hr-ideas.md`. Build only:
2. **Home "Get started" checklist** for a company with no people yet (instead of "Nothing needs you today."), with quick actions above the list.
3. **Access page leads with five roles** (Staff, Manager, HR team, Payroll preparer, Payroll reviewer); the nine checkboxes move under "Fine-tune"; "switch" becomes "permission" on this page; all warnings and the review dialog stay.
4. **Add person in two steps**: step 1 name, job title, start date (defaults to today), manager, department; step 2 "More details" with contact details and a nested "Payroll details" with its "all three or none" hint. The same component serves Edit.
5. **Settings in short plain sections**: weekday tick-boxes and a holiday date picker (the new `DateRangeFilter` in single-date mode or a plain date input), "Automatic reminders", "Who can open private areas"; Advanced stays collapsed; each HR tab gets a one-line `PageIntro`.
The two ideas the HR fact sheet flagged as needing the backend (optional leave "policy reference", company names in the platform-admin picker) are NOT part of this round; the unlinked-message dead end is a setup issue, listed for the owner.

### 6.14 Phase checkpoints

After each phase the preview on port 3100 is rebuilt from the current code with the synthetic stub (extended for new endpoints), so the owner can look and correct before the next phase. Anything found at a checkpoint is a new commit, not a rewrite.

### 6.15 Restyling what other developers added

HR shell and tab bar, cards, forms and org chart, the person profile, `RequestsPanel`, the Email preview, the workspace dock's panel and `ProposalCard` move onto the shell tokens (`--fs-*`), `PageIntro`, `StatusChips` and `DetailsRow` where they fit. Structure and logic stay; this is class and token work plus the shared pieces. Files other owners rely on (`ProposalCard.tsx`, `EmailPreview.tsx`, `lib/email-recipients.ts`) change only in classes and tokens, never in props or behaviour.

## 7. Backend additions (small, additive, defaults unchanged, owner said these are acceptable)

1. `GET /console/v1/runs`: optional `from`, `to` (timestamps) and `status` (`ok` or `error`) together with the existing `agent`, `limit`, `before_id`, `client_id`; order and the cursor stay `id DESC`. Contract tests for each parameter, for the combination, and that no parameters gives today's response.
2. `GET /console/v1/business/orders`: optional `from`, `to` (on `placed_at`), `status`, `payment_status`; plus `GET /console/v1/business/orders/counts` returning counts per status for the same filters (a new read, tenant-scoped like its sibling).
3. If the builder's query-plan check shows the two lists need it: migration **0503** adding indexes on `(client_id, created_at)` for `agent_runs` and `(client_id, placed_at)` for orders (not concurrent, so applied when quiet, as 0502 was). If the plans are fine without them, no migration. `EXPECTED_SCHEMA_VERSION` stays "0520" (the highest merged version).
4. Regenerate `docs/console_openapi.json`; update `docs/CONSOLE_API.md`.
Nothing under `csa/hr`, `csa/email` or `csa/workspace` changes.

## 8. Testing

Node: pure helpers of the four shared pieces; the activity strip's de-duplication and run-to-state mapping; order status plain-forms and counts; avatar halo contrast. Python: contract tests for the new parameters and the counts read. Browser (stubbed upstream as before): avatars have no tile on all three themes; the e-invoicing avatar renders in the Services row and the Runtime strip; chat simplification (limits text gone, mic menu, follow-up starters only prefill, jump-to-latest); Assistant activity (five rows per page, Older and Newer, arrow opens details, date range and status filters send the right query, last-five strip); Business (cards, arrow, filters send the right query, counts); Knowledge base (independent sections, one failure does not blank the page); Team (confirm steps); Email (chips and the 90-day note, confirms, no extra calls); Subscriptions (summary row hidden on failure, time zone); HR (checklist, roles-first access, two-step Add person); five widths in three themes for the new layouts. The extended preview stub serves every new endpoint.

## 9. Calls I made (owner can overrule)

- Avatar: sticker-outline halo token instead of a night palette.
- One quiet honesty line under the chat composer.
- Chat follow-up starters only prefill; Copy button on replies.
- "Last five assistants" strip built client-side from `/runs?limit=100`, not a new endpoint; shows fewer than five when fewer have run.
- Date filters use the browser's local days; the server receives instants.
- Confirmation steps added to Inbox "Queue reply", Email Delete draft / Disconnect Gmail / Switch off, and Team role change / Disable access.
- Knowledge base keeps its name for now; options listed in 6.7.
- Business management stays orders, products and store records (no invoices, bookings, customers tabs).
- Migration 0503 only if the query plans demand it.
- No label or wording rewrite of existing text, per the owner.

## 10. Left to the builder

File and component layout inside the kit, exact copy for new elements under the writing guide, calendar visual details, the e-invoicing sprite drawing, card sizes and spacing, skeleton and error states, and the query-plan check.
