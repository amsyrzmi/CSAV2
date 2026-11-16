# Overview becomes the assistant (PR 3a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Overview shows the staff assistant (one conversation shared with the dock), the customer sandbox chat moves to a "Customer preview" section under Services, Customer replies, and the assistant can navigate to Payments and Connections.

**Architecture:** Conversation state moves out of `AssistantDock` into a provider mounted once in `AppShell`; the dock and a new full-page Overview view are two views over it. The sandbox component is reused unchanged inside `CustomerRepliesPanel`; its backend, tables and voice stay. Server side, two tabs and one gate are added to the planner's closed tab set, which changes the planner prompt.

**Tech Stack:** Python 3.13 / FastAPI / Pydantic, pytest + Playwright, Next.js App Router + React, TypeScript, `node --test` for web unit tests, pyright, ruff.

**Spec:** `docs/superpowers/specs/2026-11-16-overview-assistant-design.md` (approved by the owner 2026-11-16). 3b (alive dock) is a separate plan: `docs/superpowers/plans/2026-11-16-overview-assistant-3b.md`.

`W` below means `CSA_WebConsole/csa-console/web`.

## Global Constraints

- Public repository: synthetic data only (`example.com` addresses, invented names); no real address, hostname or secret in code, tests, docs or commit messages.
- No migration, no route removed or added, `docs/console_openapi.json` unchanged. Nothing under `db/` changes.
- The sandbox backend (`csa/console/chat.py`, `csa/console/voice*.py`, `db/sql/console/chat_*.sql`, migration 0290/0440, the `Channel.WEB` gate rule, `SANDBOX_SCOPE_TEXT`) is not edited. The preview keeps its `sandbox-*` test ids and its `dockClearance` prop.
- A planner prompt change forces the FULL `assistant_smoke` before merge, run by the owner's yes inside the staging gateway container. This plan builds and unit-tests; it does not run the smoke, roll out, or touch staging, production or the home server. Stop at "PR open, CI green".
- Console copy follows `docs/CONSOLE_VOICE.md`; use `components/console/ui.tsx` and `components/console/kit/`; every new component honours dark/light tokens (`W/tests/theme-literals.test.mjs` lists files).
- `W/proxy.ts` has a hand-written page allow-list; `W/scripts/check-isolated.mjs` names source and test files by path: add every new `W` file and test there.
- PR CI runs only changed test files: for every console API call this plan adds to a page (Overview now reads the workspace thread and turn routes at mount), grep every browser test that stubs the console API and run those files by hand, one at a time (the host is memory-starved; browser tests need more than 3000 MB free).
- Run pyright on every changed Python file (`uv run pyright <files>`); never run the whole project locally (it hangs on this host: CI does it).
- Tooling quirks: a PostToolUse formatter mass-reformats after the Edit tool, so change files with Write or a small python replace script. Use the repo venv at `/home/amsyrzmi/CSAV2/.venv`. Tests need `NO_LOCAL_ENV=1 ENV=ci`; a worktree session refuses `VAR=value` prefixes and compound commands, so use a scratchpad wrapper that exports them (and puts `/tmp/fourstacks-toolbin` on `PATH` for node) and run `bash <wrapper> uv run pytest <one file>`; the memory note `worktree-session-guard` describes it.
- Commit trailers on every commit: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_012Xj1GQFBtX7jxjqQanM4hp`. Merge main into the branch; never rebase or force-push (a hook blocks both).
- Ledger: `.superpowers/sdd/2026-11-16-overview-assistant-3a/progress.md` (git-ignored).

## Review Focus

Failure modes the spec implies that no feature task would otherwise test (each has a test in the owning task):

1. **Leaving Overview mid-conversation.** A navigate-with-resume from the Overview page lands on another page, where the dock re-sends the message once; the composer text typed before and the thread are kept, and no second thread is created. (Task 5)
2. **Platform admin with no company chosen.** Overview shows the chooser, the dock is hidden, and no workspace route is called. (Task 7)
3. **Company switch on Overview** (`?client_id=` changed): the conversation resets, no turn from the other company stays visible, open cards are withdrawn. (Task 5)
4. **A card left open on Overview** when the user clicks a rail link: it is cancelled through the server before it can be confirmed on another page. (Task 5)
5. **The preview deep link for a company with no preview.** `/services?section=preview` for a company without Customer support shows Services normally with no preview section and no error; `/chat` keeps `client_id`. (Task 4)

---

### Task 1: Server: two tabs, the `chat` gate, labels and prompt

**Files:**
- Modify: `csa/workspace/contracts.py` (the `TabKey` literal, line ~62)
- Modify: `csa/workspace/rules.py` (`TAB_GATES`, `TAB_LABELS`, `WRITER_TABS`, lines ~190-215)
- Modify: `csa/workspace/planner.py` (the `navigate` line of the system prompt, ~327; add one navigate example per new tab only if the prompt already carries examples for tabs)
- Modify: `csa/workspace/smoke_cases.json`, `docs/ASSISTANT_RETEST.md`
- Test: `tests/test_workspace_contracts.py`, `tests/test_workspace_planner.py` (or `tests/test_workspace_plan_turns.py`, wherever navigate gating is tested today), `tests/test_assistant_smoke.py`

**Interfaces:**
- Produces: `TabKey` gains `"payments"` and `"connections"`. `rules.TAB_GATES["payments"] == ({"subscriptions"}, {"active"})`; `rules.TAB_GATES["chat"] == ({"customer_support"}, {"active", "suspended"})`; `connections` has no gate. `WRITER_TABS` gains `"payments"`. `TAB_LABELS` gains `"payments": "Payments"`, `"connections": "Connections"` and `"chat": "Customer preview"` (was `"Overview"`). `tab_available` and `role_allows_tab` are unchanged functions and pick the new entries up.

- [ ] **Step 1: Write failing tests.** In `test_workspace_contracts.py` change `test_tab_labels_match_the_console_navigation` so the console labels no longer alias `chat` to `overview`: `chat` is checked against `TAB_LABELS["chat"] == "Customer preview"` separately, and every other tab (now including `payments`, `connections`) must equal the `{ key, label }` pairs read from `W/lib/navigation.ts`. In the navigate-gating tests add: `tab_available("payments", READ_ONLY, [subscriptions active])` is false; `("payments", TENANT_STAFF, [])` false; `("payments", TENANT_STAFF, [subscriptions active])` true; `("payments", PLATFORM_ADMIN, [])` true; `("connections", READ_ONLY, [])` true; `("chat", TENANT_STAFF, [customer_support suspended])` true, with `[]` false, with `[customer_support off]` false. Add one planned-turn test per new tab: a model reply `{"action": "navigate", "tab": "payments"}` yields a `NavigateResult` when unlocked and an `AnswerResult` carrying the locked text (the existing `locked`/`locked_role` lines naming the tab as "Payments" and the package title) when not; `chat` locked for a company without Customer support names "Customer preview".
- [ ] **Step 2: Run each test file and see the new tests fail** (`bash <wrapper> uv run pytest tests/test_workspace_contracts.py -q`, then the planner file).
- [ ] **Step 3: Implement** the contract, rules and prompt changes above. In the prompt, the tab list becomes `(overview, services, inbox, knowledge_base, business, hr, email, subscriptions, bookings, payments, connections, activity, chat)` and `chat` is described as "the customer preview: talk to the company's bot as a customer would". Do not add other prompt text. Grep `csa/` for any other closed list of tab names (for example in `service.py`) and extend it the same way.
- [ ] **Step 4: Add smoke cases** to `smoke_cases.json` (ids `navigate-payments-en-1`, `navigate-payments-ms-1`, `navigate-connections-en-1`, `navigate-connections-ms-1`): messages "Open Payments" / "Buka Pembayaran" (preset `staff_subscriptions`) and "Open Connections" / "Buka Sambungan" (preset `staff_basic`), expect action `navigate`. Add one row for each to the checklist table in `docs/ASSISTANT_RETEST.md` (the test `test_every_smoke_case_is_in_the_checklist` fails otherwise) and update any corpus-size pin in `test_assistant_smoke.py`. Check that the existing `chat` smoke cases, if any (`grep -n '"chat"' csa/workspace/smoke_cases.json`), still make sense with the new meaning.
- [ ] **Step 5: Run** the three test files, `uv run pyright` on `contracts.py`, `rules.py`, `planner.py`, the three test files and `scripts/assistant_smoke.py`, and `uv run ruff check` plus `uv run ruff format --check` on the changed Python files. Expected: all pass.
- [ ] **Step 6: Commit** `feat(assistant): Payments and Connections tabs and a gate for the customer preview`.

### Task 2: Front end: tab mirror and the `chat` mapping

**Files:**
- Modify: `W/lib/workspace-api.ts` (`TabKey`, line ~16)
- Modify: `W/lib/assistant-view.ts` (`TABS`, `TAB_NAMES`, `navEntry`, `tabHref`, `tabLabel`, `navigateText`)
- Modify: `W/lib/assistant-copy.ts` (the `guidance_workspace_home_help` line, ~121)
- Test: `W/tests/assistant-view.test.mjs`, `W/tests/nav-visibility.test.mjs`

**Interfaces:**
- Consumes: Task 1's tab list; `navigationFor(me, packages)` and `entryForPath` from `W/lib/navigation.ts` (unchanged).
- Produces: `TabKey` includes `payments`, `connections`. `tabHref("chat", me, packages, tenant)` returns the Services href with `section=preview` when the company has Customer support `active` or `suspended` or `me.role === "platform_admin"`, else `null`; `tabHref("payments"|"connections", …)` resolves through the rail entry like every other tab. `tabName("chat")` and `tabLabel("chat", …)` are `"Customer preview"`; `tabForPath` is unchanged (a Services path is the `services` tab). New pure helper `previewAvailable(me: Me, packages: PackageState[]): boolean` (exported) holding that rule, which Task 4 reuses.

- [ ] **Step 1: Write failing node tests** in `assistant-view.test.mjs`: `tabHref("chat")` returns `/services?section=preview` (admin shell) and `/portal/services?section=preview` for a member with Customer support `suspended`; returns `null` with no Customer support; a platform admin gets `/services?section=preview&client_id=<id>`; `tabHref("payments")` follows the rail for a writer with Subscriptions and is `null` for a read-only member; `tabLabel("chat")`/`tabName("chat")` equal "Customer preview"; `navigateText("chat", …)` for a company without support returns the `navigate_unavailable` line (error true). Add a `nav-visibility` case that `payments` and `connections` rail entries exist for the roles the server gate allows (mirrors Task 1's gates).
- [ ] **Step 2: Run** `node --test W/tests/assistant-view.test.mjs W/tests/nav-visibility.test.mjs` through the wrapper; expect the new cases to fail.
- [ ] **Step 3: Implement** the mirror. `TABS` and `TAB_NAMES` gain the two tabs; `navEntry` for `chat` resolves the `services` rail entry and `tabHref` forces `section = "preview"` and applies `previewAvailable`. Reword `guidance_workspace_home_help` to end "Customer previews are on Services, Customer replies." (builder's note for the PR body: this is a static legacy guidance string, so it cannot be hidden per company as the spec asks; the line only appears on the legacy `page_hint` path).
- [ ] **Step 4: Run** the two node test files and `typecheck` (`npm run typecheck` in `W`, through the wrapper); expect pass.
- [ ] **Step 5: Commit** `feat(console): mirror the Payments, Connections and customer preview tabs in the dock`.

### Task 3: Starting ideas: data, filter and coverage test

**Files:**
- Create: `W/lib/assistant-ideas.json` (the data), `W/lib/assistant-ideas.ts` (the filter)
- Test: `W/tests/assistant-ideas.test.mjs`, `tests/test_assistant_ideas.py`
- Modify (only if no existing case fits an idea): `csa/workspace/smoke_cases.json`, `docs/ASSISTANT_RETEST.md`, `scripts/assistant_smoke.py` (new `Preset` entries)

**Interfaces:**
- Produces, in `assistant-ideas.json`: an array of `{ "id": string, "tab": TabKey | "any", "roles": ("tenant_admin"|"tenant_staff")[], "packages": string[], "en": string, "ms": string }`. `packages` are package keys that must all be `active` (empty = no requirement). In `assistant-ideas.ts`: `export interface Idea { id: string; tab: TabKey | "any"; roles: string[]; packages: string[]; en: string; ms: string }` and `export function ideasFor(ideas: readonly Idea[], opts: { tab: TabKey | null; role: Me["role"]; packages: PackageState[]; language: Language; limit?: number }): { id: string; text: string }[]`. It keeps ideas whose `tab` is the current tab or `"any"`, whose `roles` include the role (a platform admin counts as `tenant_admin`; `read_only` and `null` get none), and whose packages are all active; current-tab ideas come first, order inside a group is file order, default `limit` is 4. The module has no imports except types (it must load in `node --test` the way `overview-home.ts` does).

- [ ] **Step 1: Choose ideas from the corpus.** Draw 8 to 12 ideas, each a staff job the assistant does today (inbox summary, email a colleague, book a slot, add or open a person, ask about a training video, open a tab), across at most these tabs: `any`, `inbox`, `hr`, `bookings`, `email`, `subscriptions`, `training_library`. Take the English and Malay text **verbatim from existing `message` fields in `csa/workspace/smoke_cases.json`**; add a new case only where none fits.
- [ ] **Step 2: Write the failing Python test** `tests/test_assistant_ideas.py::test_every_idea_is_a_smoke_case_for_the_people_who_see_it`: for each idea and each of `en`/`ms`, find a smoke case whose `message` equals the text and whose `language` equals that language, where `role = case.facts.role or Role.TENANT_STAFF` is in the idea's roles and the idea's packages are a subset of the case's active packages (`scripts.assistant_smoke._PACKAGES` plus the preset's `extra_packages`, with `hr` only if the preset's `hr_status` is `active`). Fail with the idea id and language when none exists. Also assert ids are unique and both texts are non-empty, under 200 characters, with no digits-only or address content.
- [ ] **Step 3: Write failing node tests** (`assistant-ideas.test.mjs`) for `ideasFor`: current-tab ideas before `any`; a read-only member and a null role get none; packages filter; the platform admin counts as tenant admin; language selects `en`/`ms`; `limit` is respected and defaults to 4.
- [ ] **Step 4: Run both** and see them fail (`bash <wrapper> uv run pytest tests/test_assistant_ideas.py -q`; node test through the wrapper).
- [ ] **Step 5: Implement** the JSON and `ideasFor`; add any new case, its checklist row and preset.
- [ ] **Step 6: Run** both tests, `tests/test_assistant_smoke.py`, pyright on `tests/test_assistant_ideas.py` and `scripts/assistant_smoke.py`, ruff on changed Python. Expected: pass.
- [ ] **Step 7: Commit** `feat(console): starting ideas the assistant is smoke-tested on`.

### Task 4: The customer preview moves into Customer replies

**Files:**
- Modify: `W/components/console/CustomerRepliesPanel.tsx`, `W/components/console/ServicesWorkspace.tsx` (pass `me`), `W/components/console/SandboxWorkspace.tsx` (see below)
- Modify: `W/app/(admin)/chat/page.tsx`, `W/app/portal/chat/page.tsx` (redirects), `W/proxy.ts` only if the allow-list needs it
- Modify: `W/scripts/check-isolated.mjs`, `W/tests/theme-literals.test.mjs` if file lists change
- Test: `tests/test_sandbox_chat_browser.py`, `tests/test_services_browser.py`, `tests/test_customer_replies.py` (only if the panel's API changes), `W/tests/customer-replies.test.mjs`

**Interfaces:**
- Consumes: `previewAvailable` (Task 2); `SandboxWorkspace` props `{ me: Me; chromeless?: boolean; tenant?: string }` (its chromeless mode renders the chat alone, filling its container).
- Produces: `CustomerRepliesPanel({ tenantId, me })` renders, under the persona form or summary, a section with a toggle button (`data-testid="customer-preview-toggle"`, `aria-expanded`, copy "Customer preview: talk to your bot as a customer would") and a region (`data-testid="customer-preview"`, fixed height about 32 rem) that mounts `SandboxWorkspace chromeless` only while expanded, loaded with `next/dynamic` so Services stays light. It is expanded on first render when the page URL has `section=preview`. It shows for everyone who sees the panel, including read-only members. `SandboxWorkspace` loses its non-chromeless branch (the heading, tenant select and card wrapper: nothing renders it) and its `chromeless` prop becomes unnecessary; keep the `Sandbox` function, test ids and `dockClearance` as they are.

- [ ] **Step 1: Write failing browser tests.** In `test_sandbox_chat_browser.py` change the entry point of the existing sandbox tests from Overview to Services with `?section=preview` (one shared helper that opens it, waits for `customer-preview`, and leaves the assertions on `sandbox-*` ids unchanged). Add tests: the toggle collapses and expands, and the chat is not mounted (no `/console/v1/chat` request) while collapsed; a read-only member sees and can open the preview; a company without Customer support has no preview section and no error at `?section=preview` (Review Focus 5); `/chat` and `/portal/chat` redirect to the Services URL keeping `client_id`. In `test_services_browser.py` assert the preview section exists under the Customer replies panel for a company with Customer support `active`.
- [ ] **Step 2: Run** `bash <wrapper> uv run pytest tests/test_sandbox_chat_browser.py -q` (then the services browser file), one at a time; expect the new and the moved tests to fail.
- [ ] **Step 3: Implement** the section, the prop, the dynamic load, the redirects (preserve every query parameter and add `section=preview`; the `chat` pages stay pure redirects) and the dead-branch removal. Update `check-isolated.mjs` and `theme-literals.test.mjs` if a path changed.
- [ ] **Step 4: Run** both browser files, `customer-replies.test.mjs` and `typecheck`; grep every browser test that renders Services (`grep -ln "services" tests/test_*browser*.py`) and run those that stub the console API, one at a time. Expected: pass. (Overview still mounts the sandbox in this task; Task 7 removes that.)
- [ ] **Step 5: Commit** `feat(console): customer preview under Customer replies`.

### Task 5: One conversation state, mounted once

**Files:**
- Create: `W/lib/assistant-conversation.tsx` (provider and hook)
- Modify: `W/components/console/AssistantDock.tsx` (now a view), `W/components/AppShell.tsx` (mount the provider inside `PageContextProvider`, around `children` and the dock)
- Modify: `W/scripts/check-isolated.mjs`
- Test: `tests/test_workspace_browser.py` (existing, must pass **unchanged** as the characterization of the dock), plus new cases below; `W/tests/assistant-view.test.mjs` for any pure helper that moves

**Interfaces:**
- Consumes: `dockTenant`, `effectiveContext`, `tabForPath`, `tabHref`, `navigateLandingPath`, `resumeContext`, `isHrPagePath`, `openProposals`, `latestLanguage`, `typedMessage`, `originFor`, `isAnswerable` (all in `assistant-view.ts` today), `workspace-api.ts` calls, `usePageContext`.
- Produces:
  ```ts
  export interface Outgoing { text: string; origin: string }
  export interface AssistantConversation {
    tenant: string; me: Me; packages: PackageState[];
    loaded: boolean; ensureLoaded(): void;          // first load of thread + turns; idempotent
    turns: WorkspaceTurn[]; outcomes: Record<string, ProposalOut>; origins: Record<string, string>;
    body: string; setBody(value: string): void;
    busy: boolean; error: string; retry: boolean; resumeTab: TabKey | null;
    context: PageContext | null; language: Language;
    ask(outgoing: Outgoing, fromInput?: boolean): void;
    retrySame(): void; dismissRetry(): void;        // the dock's "retry_same" and "start_new"
    settle(proposal: ProposalOut): void; newConversation(): void;
  }
  export function AssistantProvider(props: { me: Me; packages: PackageState[]; children: React.ReactNode }): JSX.Element;
  export function useAssistantConversation(): AssistantConversation | null; // null when dockTenant() is null
  ```
  The provider is keyed by tenant (a change of company discards the state and withdraws open cards, as the dock does on unmount today). `Attempt`, `Resume`, `send`, `navigate`, `fireResume`, the withdraw-on-tab-change effect and the unmount cancel move over unchanged. View state stays in the dock: `open`, focus refs, the textarea auto-size, scroll refs. `ensureLoaded` replaces the dock's `open`-triggered load, so the dock calls it when it opens and the Overview view calls it on mount.

- [ ] **Step 1: Run the characterization first.** `bash <wrapper> uv run pytest tests/test_workspace_browser.py -q` on the untouched branch; record the pass list in the ledger. This file is the safety net: it must pass unchanged after the refactor.
- [ ] **Step 2: Write failing tests for the new guarantees** (browser, using the dock's existing stubs): (a) with the dock open and text typed in the composer, a navigate-with-resume turn lands on the target page and the resume turn is sent once, with no second `POST /conversations` (Review Focus 1); (b) a card open when a rail link is clicked is cancelled through `POST /proposals/{id}/cancel` before the page changes (Review Focus 4); (c) switching `?client_id=` as platform admin ends the conversation: the new company's dock starts empty and the old company's open card was cancelled (Review Focus 3).
- [ ] **Step 3: Implement** the provider and the hook; reduce `AssistantDock` to a view over the hook (no behaviour change), mount the provider in `AppShell`.
- [ ] **Step 4: Run** `tests/test_workspace_browser.py` (unchanged tests plus the new ones), `W` node tests for assistant view and page context, `typecheck`, `lint` (`npm run lint`). Expected: the previously recorded passes still pass, new tests pass. Also grep for other browser tests that mount `AppShell` pages with the dock on and stub the workspace routes; run the ones that stub them.
- [ ] **Step 5: Commit** `refactor(console): one assistant conversation shared by every view`.

### Task 6: Shared view parts

**Files:**
- Create: `W/components/console/assistant/AssistantTurns.tsx` (turn list, working row, resume and error notices, the per-result renderer now named `TurnResult` in the dock), `W/components/console/assistant/AssistantComposer.tsx` (the form with the textarea, send, retry controls)
- Modify: `W/components/console/AssistantDock.tsx` (uses both), `W/scripts/check-isolated.mjs`, `W/tests/theme-literals.test.mjs`
- Test: `tests/test_workspace_browser.py` (unchanged, characterization)

**Interfaces:**
- Consumes: `AssistantConversation` (Task 5).
- Produces: `AssistantTurns({ conversation, listRef?: Ref<HTMLDivElement> })` renders what the dock's scroll region renders now, with the same `data-testid`s (`assistant-turn`, `assistant-working`); `AssistantComposer({ conversation, inputId, inputRef })` renders the form with the same ids (`assistant-input`, `assistant-send`). Both are layout-free (no fixed width or max height): the dock and the Overview page give them their box.

- [ ] **Step 1: Re-run** `tests/test_workspace_browser.py` on the branch head and record it.
- [ ] **Step 2: Extract** the two components from the dock by moving code, not rewriting it; the dock keeps the header, the open/close behaviour and the box.
- [ ] **Step 3: Run** `tests/test_workspace_browser.py` unchanged, `typecheck`, `lint`, theme-literal and isolated checks. Expected: identical pass list.
- [ ] **Step 4: Commit** `refactor(console): the assistant's turn list and composer as shared parts`.

### Task 7: Overview is the assistant

**Files:**
- Create: `W/components/console/overview/AssistantOverview.tsx`
- Modify: `W/components/console/overview/OverviewHome.tsx`, `W/lib/overview-home.ts`, `W/components/console/AssistantDock.tsx` (hides on Overview), `W/components/console/overview/WorkspaceHome.tsx`, `W/lib/assistant-view.ts` (the `chat → overview` alias comment, now stale), `W/scripts/check-isolated.mjs`, `W/tests/theme-literals.test.mjs`
- Modify: `W/lib/assistant-copy.ts` (empty-state headline and the dock-clearance wording if any)
- Test: `tests/test_overview_browser.py`, `W/tests/overview-home.test.mjs`, `W/tests/assistant-view.test.mjs`

**Interfaces:**
- Consumes: `useAssistantConversation`, `AssistantTurns`, `AssistantComposer` (Tasks 5, 6); `ideasFor` and `assistant-ideas.json` (Task 3); `previewAvailable` is not used here.
- Produces: `overviewBody(packages: PackageState[] | null, assistant: boolean): "assistant" | "home"` returns `"assistant"` when `assistant` is true and `"home"` otherwise; a second export `showsRepliesBanner(packages: PackageState[] | null): boolean` is true when the read failed (`null`) or Customer support is `active` or `suspended`. `OverviewHome` passes `assistant = useAssistantConversation() !== null`, keeps the tenant chooser, the status strip and the banner (shown above the assistant when `showsRepliesBanner`), and renders `AssistantOverview` as the body. `AssistantOverview` renders the conversation, the composer, a "new conversation" action, and, when the thread is empty and loaded, idea chips from `ideasFor(…, { tab: "overview", … })` (a chip fills the composer and sends nothing; `data-testid="overview-ideas"`); it calls `ensureLoaded()` on mount and uses the page's height (`data-fill` layout stays). `AssistantDock` returns `null` when `tabForPath(pathname, …)` is `"overview"`. `WorkspaceHome` stays as the fallback body and drops the "Ask the assistant at the side of the screen." line if nothing can show it any more.

- [ ] **Step 1: Write failing tests.** Node (`overview-home.test.mjs`): the new `overviewBody` and `showsRepliesBanner` truth tables (read failed, support active, suspended, absent). Browser (`test_overview_browser.py`): replace the tests that assert a sandbox chat on Overview with: (a) tenant staff of a Customer-support company see the status strip, the banner when replies are off, the conversation and `overview-ideas`; a chip fills the composer and sends nothing (assert no turn request); (b) an employee-only company sees the assistant and no banner; (c) a read-only member, and a gateway with the assistant flag off, see `WorkspaceHome` and neither the composer nor the dock; (d) a platform admin without `client_id` sees the chooser, no dock, and the stub records no workspace request (Review Focus 2); (e) asking a question that returns a navigate result moves to the target tab and the dock there shows the same turn list, with one thread; (f) the dock button is not on Overview and is on every other page; (g) the existing strip, redirect and company-switch tests stay and pass. Keep the `dockClearance` test only if its subject still exists; otherwise move it to the preview tests of Task 4.
- [ ] **Step 2: Run** `node --test` for the two web files and `bash <wrapper> uv run pytest tests/test_overview_browser.py -q`; expect the new tests to fail.
- [ ] **Step 3: Implement** the page, the rule, the dock hide, the `WorkspaceHome` line and the stale comment. Remove the `SandboxWorkspace` import from `OverviewHome` (Task 4 already mounts the preview under Services).
- [ ] **Step 4: Run** `tests/test_overview_browser.py`, `tests/test_workspace_browser.py`, `tests/test_sandbox_chat_browser.py`, `tests/test_services_browser.py` one at a time, then every other browser test whose page loads Overview or `AppShell` with `me.workspace_assistant` true and stubs the console API (`grep -ln "workspace_assistant" tests/test_*browser*.py`); Overview now reads the workspace thread and turn routes at mount, so each of those stubs needs the two routes. Then node tests, `typecheck`, `lint`. Expected: pass.
- [ ] **Step 5: Commit** `feat(console): Overview is the assistant`.

### Task 8: Documentation, checklist and whole-branch checks

**Files:**
- Modify: `docs/ASSISTANT_RETEST.md` (retest rows), `docs/PROGRESS.md` (short entry), `docs/CONSOLE_API.md` (a note under "Private sandbox assistant (M4)" that the chat is reached from Services, Customer replies), `docs/CONSOLE_VOICE.md` if it names Overview as the chat's home, `W/README.md` and `CSA_WebConsole/csa-console/STATE.md` if they place the chat on Overview

- [ ] **Step 1: Retest rows** for the owner (manual): Overview shows the assistant and ideas for a Customer-support company and an employee-only company; a chip fills and does not send; "open Payments" and "open Connections" navigate (English and Malay) and a read-only member is refused Payments; "open the customer preview" lands on Services with the preview open; the preview behaves as the old Overview chat (persona set and cleared, replies Off banner, a held answer); a read-only member sees `WorkspaceHome`. Fix any existing row that says the sandbox is on Overview (lines about "the Overview chat previews…", "chat as a customer in the sandbox (Overview)") to say Customer preview on Services.
- [ ] **Step 2: Docs.** PROGRESS entry (expect a merge conflict here later: keep both sides); the CONSOLE_API note; stale mentions found by `grep -rn "Overview" docs/CONSOLE_API.md docs/CONSOLE_VOICE.md CSA_WebConsole/csa-console/STATE.md W/README.md` that call Overview the sandbox chat.
- [ ] **Step 3: Checks.** Confirm `git diff origin/main --stat -- db docs/console_openapi.json` is empty; run the changed Python test files one at a time (`test_workspace_contracts`, `test_workspace_planner` or the file Task 1 changed, `test_assistant_smoke`, `test_assistant_ideas`), the browser files named in Tasks 4 to 7, `node --test` for every changed web test, `npm run lint`, `npm run typecheck`, `npm run check:isolated`; pyright on every changed Python file; `uv run ruff check` and `ruff format --check` on changed Python.
- [ ] **Step 4: Commit** `docs: Overview assistant retest rows, progress and console notes`.
- [ ] **Step 5: Whole-branch review** on the most capable model, one fix wave, one scoped re-review (per the subagent-driven-development skill), then merge main into the branch, push, open the PR with `gh api -X POST repos/amsyrzmi/CSAV2/pulls --input <json>` (body ends with the "Generated with Claude Code" line and the session URL), watch CI to green, and tell the owner: the PR number, that the planner prompt changed, and that the full `assistant_smoke` needs their run on staging before merge (`docker exec csa-staging-gateway-1 python -m scripts.assistant_smoke`, owner agreed 2026-11-16). Do not roll out.
