# Overview becomes the assistant: one conversation, a customer preview in Customer replies, an alive dock (assistant PR 3)

Status: design for the owner's approval, 2026-11-16. Programme: assistant refinement (PR 1 prompts #85, PR 2 engine #88, **PR 3 this**) and item 5 of the business surfaces programme. No code is written until the owner approves this document. Items marked **(call)** are decisions I took on the owner's instruction "do what's appropriate and if the sandbox isn't necessary just remove it"; the owner can overrule any of them.

## Intent

Staff land on Overview and today get a customer-bot practice chat, while the assistant that can actually do their work sits in a corner button. The owner wants the home screen to be the assistant: the same conversation, planner, confirmation cards and navigation as the dock, starting from useful ideas. The dock button should feel alive instead of being a static label. Admins still need a way to see what customers get; that moves out of the home screen.

## Findings from the code (2026-11-16, main 04db1e63)

- Overview **is** the sandbox chat. `OverviewHome` renders `SandboxWorkspace chromeless` (or the small `WorkspaceHome` when Customer support is not active). There is no other sandbox page; `/chat`, `/portal/chat` and `/workspace` only redirect.
- The sandbox is **the real customer pipeline on the web channel**: `enqueue_sandbox_turn` (`csa/console/chat.py`) puts an envelope into the ingress queue and the worker runs the customer graph. It is the only way an Admin can run the bot from the console, and it is what the persona retest rows, the Malay calibration and the owner replay checks use. Its footprint: migration 0290 (`console_chat_participants`, `console_chat_threads`, `console_chat_turns`), 15 SQL files, 10 `/console/v1/chat` paths, the `Channel.WEB` gate rule from the Overview alignment design, `SANDBOX_SCOPE_TEXT` and its guards in `csa/turn/deps.py`, and voice sessions whose profile is locked to `sandbox`.
- The assistant dock already navigates: the planner's `navigate` action is checked by role and package, `AssistantDock.navigate()` opens the page (and, with `resume`, re-sends the user's message after landing). The planner's `TabKey` has 12 tabs; Payments and Connections, which exist in the rail, are not navigable.
- Conversation state lives inside the 706-line `AssistantDock`; the thread is stored server-side (the first thread is reused) but pending cards, the resume step and the composer text are component state.
- `AgentAvatar` is inline SVG with CSS-only frames: `idle` (4 frames, 1 s), `working` (8 frames, 1.2 s, 48 px only), `wake` (once, 1.5 s). `useMotionAllowed` already stops all of it for `prefers-reduced-motion`, a hidden tab, an offscreen element and the product pause preference.
- Read-only members never get the dock (decision 7 of the workspace assistant design; the backend refuses them too). The `/console/v1/chat` routes accept any signed-in human member.
- The business surfaces programme placed this PR last so it "renders the cards that product management (PR 4) adds". `ProposalCard` renders any card by its title, summary and fields, and `plan` steps by kind, so a later kind needs only its `card()` on the server. PR 4 does not block this PR.

## Goals

- Overview shows the assistant conversation full page for everyone who has the dock today, for every company type (customer support, employee-only, or both). It is the same conversation as the dock: a thread started on Overview continues in the dock on the next page and the other way round.
- Navigation from Overview works as it does from the dock, and reaches Payments and Connections too.
- Starting ideas on Overview and on the dock button that are role- and package-aware and **never fail**: each offered idea is covered by an assistant smoke case.
- An Admin can still run the customer bot as a customer, from Services, Customer replies, next to the persona fields it previews.
- A dock button with a living avatar that reacts to hover, focus and click and occasionally offers one idea, within the existing motion rules.

## Non-goals

- Deleting the sandbox backend, its tables, routes, voice sessions or the web-channel gate rule. No migration, no data change on staging or production.
- New pixel art. The avatar uses its existing idle, working and wake frames.
- Voice (still deferred; the Overview composer leaves room for a mic button later).
- A planner change beyond two navigable tabs, new proposal kinds, count-aware ideas such as "3 replies are waiting" (a follow-up; ideas here are static), read-only members getting the assistant.
- Product management cards (PR 4).

## Anti-goals

- A second conversation store, or two threads for one user, or a resume step lost because the user left Overview.
- The Overview assistant able to do more than the dock: same planner, same registry, same confirmation, same role and package rules.
- Motion that ignores `prefers-reduced-motion` or the pause preference, a bubble that steals focus or is announced as a live region, or an idea that fails when sent.
- The customer preview disappearing for a company that has Customer replies (the retest path must survive).

## Constraints

- Public repository: synthetic data only. Console copy follows `docs/CONSOLE_VOICE.md` and uses `components/console/ui.tsx` and the kit in `components/console/kit/`. Portal twins move with their pages (the Overview page and `OverviewHome` are shared; Services has a portal twin).
- A planner prompt change forces the **full** `assistant_smoke` before merge; it runs inside the staging gateway container and needs the owner's yes. The owner runs the retest rows after rollout.
- PR CI runs only changed test files: grep every browser stub of a call that is added or removed and run those files by hand before pushing. `W/proxy.ts` has a hand-written page allow-list; `check-isolated.mjs` and `theme-literals.test.mjs` name files by path.
- Migration numbers: none needed. The rollout of staging is the other session's; this work stops at "PR open, CI green".

## Approaches

**Overview.** A (chosen): Overview is a full-page view over the one shared assistant conversation; the dock hides on Overview and takes over elsewhere. B: Overview mounts its own second assistant instance. Rejected: two threads, duplicated resume and card-withdraw logic, and the conversation would restart on every navigation. C: Overview keeps a small home and only opens the dock. Rejected: it is not "Overview becomes the assistant".

**The sandbox.** A (chosen **(call)**): remove the Overview surface, keep the backend, and mount the existing sandbox component under Customer replies as "Customer preview". B: delete everything. Rejected: it drops the only way to test the real customer pipeline from the console, needs a migration that drops three tables and their RLS, strips the web-channel gate and scope-text guards the preview-as-auto rule depends on, and removes voice sessions; the tests and retest rows that use it would have no replacement. C: leave it on Overview as a tab. Rejected: contradicts the goal.

**The alive dock.** A (chosen): existing frames plus two small CSS reactions and at most one idea bubble. B: a roaming or pointer-following character with new frames. Rejected: art cost, constant motion on a work screen, and a harder accessibility story. C: no change. Rejected by the owner's request.

**Pull requests (call).** Two, in order, so each lands and is retested on its own: **3a** Overview, preview move, navigation widening (planner prompt change, full smoke); **3b** alive dock and ideas (front end only). They share no files except the idea list, which 3a introduces. The owner can ask for one PR.

## Design

### 3a. Overview as the assistant

**Which body Overview shows.** Platform admin without a chosen company: the existing company chooser. Anyone for whom `dockTenant()` returns a company: the assistant page. Everyone else (read-only members, or the gateway's `WORKSPACE_ASSISTANT_ENABLED` off): `WorkspaceHome`, the small home of links, as an employee-only company has today. The status strip stays above every body. `CustomerRepliesBanner` stays above whichever body is shown when Customer support is active or suspended, because "your customers aren't getting replies" is still the first thing an Admin must know. A failed package read keeps today's rule: nothing is removed because a read failed (the page shows, and so does the banner: an unknown package state counts as Customer support being on, as the base Overview did).

**One conversation, two views.** The conversation state moves out of `AssistantDock` into a provider mounted once in `AppShell` (the thread id, turns, card outcomes, origins, pending attempt, resume step, composer text, busy and error). `AssistantDock` becomes the floating view and the new `AssistantOverview` the page view over it. On the Overview path the dock hides; on every other path the Overview view is not mounted. Because the state sits above both, leaving Overview by a `navigate` result keeps the resume step (the message is re-sent after landing, as now), and an open card is withdrawn exactly as the dock does today. A tenant change still ends the conversation (the provider is keyed by tenant). Mechanism details (provider versus portal) are the builder's, with the provider preferred; the requirement is one state and no remount that loses the composer text or a pending card.

**What the page shows.** The conversation (the same turn renderer, `ProposalCard`, `PlanSteps`, clarification and directory buttons), the composer, a "new conversation" action, and when the thread is empty the starting ideas as chips. A chip only fills the composer; nothing is sent by a click (as today). The page uses the page's own height instead of the dock's fixed 40 rem box.

**Starting ideas.** One list in `lib/assistant-copy.ts`: per tab and per language (English and Bahasa Melayu), each idea carrying the role and the packages it needs. The chips for a user are the ideas their role and active packages allow, for the current tab. Every idea must be covered by a case in `csa/workspace/smoke_cases.json`: a case whose `message` equals the idea's text, whose `language` equals the idea's language, and whose `facts.preset` holds the role and packages the idea requires (**call**: matching the text alone would pass an idea shown to an Admin with HR even if the only case runs as `staff_basic`). A test fails for an idea with no such case. New cases, and new presets where none fits, are added for ideas that have none; the builder keeps the idea list short enough that this stays a small number of cases (the corpus has 69 today, 31 of them `staff_basic`). The old customer-question chips (`sandbox-chips`) move with the preview (they are the preview's own, about customers).

**Navigation.** The dock's flow is reused unchanged (cancel open cards, open the page, optional resume). Two tabs are added to the planner's closed `TabKey` set, `payments` and `connections`, mirrored in `lib/workspace-api.ts`, `tab_available` and the package and role gates in `csa/workspace/rules.py`, with the gates copied from the rail (**call**): Payments needs the Subscriptions package and a writer role (`payments` joins `WRITER_TABS`), Connections has no gate, and the planner prompt's tab list; the existing contract test that keeps `lib/navigation.ts` and the server gates in step (`tests/test_workspace_contracts.py`) is extended. Admin-only pages (Team, Billing, Credit, Password, System, Tenants) stay unreachable by the assistant. Read-only members have no assistant, so no change to who may navigate. The `chat` tab key now means "the customer preview": it opens Services with the preview section expanded (`?section=preview`), not Overview, and its label is "Customer preview" instead of "Overview" (**call**). `chat` gets a `customer_support` gate (active or suspended, like `inbox`) in `TAB_GATES`, because the preview exists only where Customer replies does (**call**; today `chat` has no gate and is offered to everyone). For a company without Customer support the planner answers with its normal locked-tab line, and the dock's copy about previews is not shown to it. `/chat` and `/portal/chat` redirect to Services; without Customer support Services simply shows no preview section. No prompt wording changes beyond the tab list and one navigate example for each new tab, English and Malay; the full smoke runs.

**Customer preview on Services (call).** A collapsed section "Customer preview: talk to your bot as a customer would" is its own component, `CustomerPreview`, rendered by `ServicesWorkspace`: under the Customer replies panel when Customer support is active, and on its own row under the Customer support card when it is suspended (no persona form then; ledger Ruling 3). It mounts the existing sandbox component (threads, polling, history, voice) unchanged, loaded on demand so Services stays light. Visible to everyone who sees the Customer support card (the chat routes accept any human member, and a preview sends nothing to a customer). Its banner copy keeps saying replies are off or held when they are; the preview-as-auto gate rule is unchanged. The component keeps its `sandbox-*` test ids and its `dockClearance` prop (the dock button still sits at the corner of Services). The user-facing name is "Customer preview"; code names stay `sandbox` to keep the diff to the moves that matter. Dock copy "Customer sandbox conversations are separate" becomes "Customer previews are on Services, Customer replies".

**What is removed.** The chat branch of `overviewBody()` and `OverviewHome`'s mount of the sandbox, the `chat` to Overview alias in `assistant-view.ts` and its comment, and the Overview tests that assert a chat on Overview. **What stays:** every backend file, route, table, SQL file, the voice routes, the gate rule, `SANDBOX_SCOPE_TEXT`, the staged data (existing threads stay readable in the preview). `docs/CONSOLE_API.md` keeps its sandbox section with a note that the chat is reached from Services; the openapi file is regenerated with the repo tool only if a route changes (none does).

**Tests that move.** `tests/test_overview_browser.py` (21 tests): the chat tests become assistant-page tests (page renders, chips fill and do not send, a navigate result moves to a tab and resumes, the small home for read-only and flag-off, the banner placement); the strip, redirect and small-home tests stay. `tests/test_sandbox_chat_browser.py` (33 tests): the entry point changes from Overview to the preview section on Services; assertions stay. Browser tests that stub the console API are grepped for any new call (the lesson of #84 and #85): the provider keeps the dock's lazy first load, but on Overview the page is open at mount, so Overview now reads the workspace thread and turn routes on first load; every Overview browser test that stubs the console API stubs those routes too. Node tests: `overview-home.test.mjs`, `assistant-view.test.mjs`, `nav-visibility.test.mjs`, `shell-state.test.mjs`, `sandbox-chat.test.mjs` are updated; `check-isolated.mjs` and `theme-literals.test.mjs` list the new files.

### 3b. The alive dock

The collapsed dock button becomes the assistant avatar (32 px) beside the label, replacing the speech-bubble icon.

- **At rest:** the avatar plays its idle loop when `useMotionAllowed` says so, and draws frame 0 otherwise, as everywhere else. While a turn is in flight and the panel is closed, it plays the working loop.
- **Reacts:** on hover or keyboard focus the wake animation plays once; on click the button does one short hop (a 400 ms CSS translate) as the panel opens. With motion not allowed there is no wake and no hop.
- **Proposes ideas:** when the panel has been closed and the page untouched for 20 seconds, the button shows one idea for the current tab in a small bubble above it (the same idea list as 3a, filtered by role and packages, in the user's language). The bubble is a button: activating it opens the panel with the idea in the composer (not sent). It has a close control. At most one bubble per page visit; a dismissed idea does not return in that browser session (kept in `sessionStorage`); the bubble never opens while a card awaits a decision, while the panel is open, or on Overview (where the page itself shows the ideas). It is not a live region and takes no focus.
- **Motion rules:** one gate, `useMotionAllowed(ref)`, for the avatar loops, the wake and the hop (so `prefers-reduced-motion`, a hidden tab, offscreen and the product pause preference all apply). The bubble itself needs no motion and still appears with motion off. The hop and the bubble use no transform on the panel itself.
- **What does not change:** the panel, the cards, the confirm flow, the dock's gating (flag, role, platform admin needs `?client_id=`).

## What must be true when it is done

- Overview, signed in as tenant staff of a company with Customer support, shows the status strip, the banner if replies are off, and the assistant with ideas; a question that needs a page ("open Payments") moves there and the answer continues; a card on Overview is confirmed with the same server flow as the dock's.
- Overview for an employee-only company shows the assistant (no banner); for a read-only member, or with the assistant flag off, it shows the small home.
- A conversation started on Overview is the same thread in the dock on the next page.
- "Open the customer chat" opens Services with the preview expanded; `/chat` and `/portal/chat` land there; a customer turn in the preview behaves exactly as the sandbox does today (gate rule, held lines, voice).
- No migration, no route removed, `docs/console_openapi.json` unchanged.
- Every idea shown is a smoke prompt; the full `assistant_smoke` has no new failures, including the two new navigate cases in English and Malay.
- Reduced motion or paused: no animated avatar, no hop; ideas and bubble still work.

## Left to the builder

Provider versus portal for the single conversation state; the page layout and idea copy (English and Malay, voice of `CONSOLE_VOICE.md`); the exact list of ideas per tab within the rules above; bubble placement, the 20 second timer's reset rules and the `sessionStorage` key; the preview section's layout and lazy loading; the new smoke cases; the split of tests between Playwright and node tests.

## Verification

- Contract: `TabKey` equal on server and client, gates for `payments` and `connections` equal to the rail's, `chat` resolves to Services.
- Browser: Overview as assistant for the three company types and for read-only and flag-off; chips fill and never send; navigate with resume across Overview, Services and back; one thread across dock and Overview; preview section end to end through the existing sandbox tests; reduced motion and pause for the dock.
- Planner: pins for the two new tabs; the **full** `assistant_smoke` on staging with the owner's yes (the prompt changed).
- Owner retest rows (added to `docs/ASSISTANT_RETEST.md`): Overview assistant, ideas, navigation to Payments and Connections, the preview in Customer replies with and without a persona, the alive dock with reduced motion on.
- Not built or measured here: count-aware ideas, voice, new avatar art.
