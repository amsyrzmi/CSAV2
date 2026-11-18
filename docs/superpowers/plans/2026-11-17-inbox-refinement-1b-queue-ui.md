# Inbox refinement, part 1b: the queue screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Inbox page becomes a work queue screen: tabs with live counts, filters, sort and saved views, three-line rows that show state, wait, priority and assignee, a thread header with the right primary action (take it, resolve, snooze, assign, priority, topic), bulk actions and a settings panel, all on the API that shipped in PR #153.

**Architecture:** Front end only. A pure module (`lib/inbox-queue.ts`) holds the tab definitions, query building, time and state wording and action rules so node tests cover them; a data layer of hooks wraps the new routes; the existing Inbox components (`Inbox.tsx`, `ConversationList.tsx`, `Thread.tsx`, `OpenConversation.tsx`) are reshaped around a queue bar, a new row and a thread action bar. No backend change. Takeover, reply confirmation, release, held-reply cards, the customer card and the message log keep their behaviour.

**Tech Stack:** Next.js App Router, React, TypeScript, Tailwind classes over the `--fs-*` tokens, Lucide icons, `node --test`, Playwright through pytest.

**Spec:** `docs/superpowers/specs/2026-11-16-inbox-refinement-design.md`, section 14 (the screen) and sections 4 to 6 (the model); the API as built is in `docs/CONSOLE_API.md`, "Inbox work queue". `W` below means `CSA_WebConsole/csa-console/web`. The visual rules are `ui-guide/UI_GUIDE.md` and `docs/CONSOLE_VOICE.md`.

## Global Constraints

- Public repo, synthetic data only. Never read anything under `/srv/csa-staging/secrets`. No staging rollout, deploy, home-server action or GitHub setting change: stop at "PR open, CI green" and tell the owner. No backend, migration or OpenAPI change in this plan (if a route is missing something, report it as BLOCKED with the evidence).
- Theme: tokens only (`--fs-*`, the existing classes in `W/components/console/ui.tsx`, `StatusLabel` tones, `StatusChips`, `CustomerAvatar`, `ListState`, `SlideOver`, `Notice`); no hex or hsl literals (`W/tests/theme-literals.test.mjs` enforces it, add every new component file to it and to `W/scripts/check-isolated.mjs`); no new colours, fonts or shadows other than overlays; meaning always in words (icon plus text, never colour alone); 40 px controls (44 px on coarse pointers); plain words per `docs/CONSOLE_VOICE.md`; times in the company timezone (`Asia/Kuala_Lumpur`, fixed +08:00) with the absolute time in a `title`.
- Motion only through `useMotionAllowed` (`W/components/console/fs/useMotionAllowed.ts`); nothing moves when it is false.
- A PostToolUse formatter mass-reformats after the Edit tool: change files with Write or small python replace scripts and check `git diff --stat`. Worktree sessions refuse `VAR=value` prefixes and compound commands: use the wrapper `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/31dc140d-0929-4994-9647-6e6e38e51833/scratchpad/ci2.sh <cmd>` (`node --test`, `python -m pytest`, `pyright`, `ruff`). Web checks from `W`: `node node_modules/next/dist/bin/next typegen` then `node node_modules/typescript/bin/tsc --noEmit` (ignore the pdfjs-dist, PageCanvas, PdfViewer, Thumbnails and training-pdf errors, they are environmental), `node node_modules/.bin/eslint <files>`.
- Host memory is tight: run test files **one at a time**, check `free -m` first (browser files need more than 3000 MB available), never the whole suite (CI runs it). CI runs only changed test files on a PR: **grep every browser file and fixture that drives the Inbox** (`grep -ln "inbox-row\|data-conversation-id\|inbox-filter\|inbox-search\|/inbox" tests/*browser*.py tests/test_packaged_console.py`) and run those by hand; the slow packaged acceptance test (`tests/test_packaged_console.py::test_packaged_customer_inbox_browser_acceptance`) drives the Inbox through the real UI and must be updated and run once locally (Task 10).
- Keep existing `data-testid`s on elements that still exist (`inbox-search`, `inbox-search-clear`, `inbox-row-time`, `inbox-row-preview`, `inbox-channel-badge`, `inbox-thread`, `inbox-composer`, `inbox-reply-confirm*`, `inbox-details-button`, `inbox-new-messages-pill`, `inbox-handoff-note`); a testid that disappears is renamed deliberately in the task that removes it, with its tests.
- Commit trailers: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_012Xj1GQFBtX7jxjqQanM4hp`. Merge main, never rebase or force-push; PR via `gh api -X POST repos/amsyrzmi/CSAV2/pulls --input <json>`; body ends with the "Generated with Claude Code" line and the session URL.
- Ledger: `.superpowers/sdd/2026-11-17-inbox-refinement-1b-queue-ui/progress.md` (git-ignored).

## Review Focus

1. **Existing Inbox flows unharmed.** Take over, the two-step reply confirmation, release, held-reply approve, edit and reject, J and K, search debounce and clear, the customer card and slide-over, the packaged acceptance flow. (Tasks 3 to 6 and 10)
2. **The right action, in words.** A button shows only when the server will allow it for this role and item (tenant staff never see Assign or "Take it from {name}"); a 409 `held_reply_pending`, a 409 `customer_wrote`, a stale version, a 403 and a 503 each end in a plain sentence and a refresh, never a raw code. (Tasks 1, 2, 5)
3. **Honest counts and clocks.** A failed counts request shows "–", never 0; a wait shows the server's `needs_staff_since`, not the time the page opened; an item claimed by someone with no display name is still shown as claimed. (Tasks 1, 3, 4)
4. **Bulk results.** The summary says exactly what happened per outcome, failed items stay selected, and `as_of` from the list is what the request sends as `seen_at`. (Task 6)
5. **Every width and theme.** No horizontal page scroll at 320, 390, 768, 1024 and 1440 px in each theme; keyboard order, focus return and Escape for every menu, sheet and dialog; reduced motion. (Tasks 3, 7 to 9)

---

### Task 1: The queue model, pure and tested

**Files:**
- Create: `W/lib/inbox-queue.ts` (no imports except types, loadable by `node --test` like `lib/inbox-api.ts`), `W/tests/inbox-queue.test.mjs`
- Modify: `W/lib/inbox-api.ts` (types), `W/scripts/check-isolated.mjs`

**Interfaces:**
- Produces in `inbox-api.ts`: `Conversation` gains `queue_status`, `queue_reason`, `topic`, `priority`, `assignee_id`, `assignee: {id: string; display_name: string | null} | null`, `claimed_at`, `needs_staff_since`, `due_at`, `snoozed_until`, `last_customer_at`, `last_staff_reply_at` (all as in `docs/CONSOLE_API.md`, nullable where the API is); `ConversationPage` gains `counts: QueueCounts | null`, `as_of: string`; `QueueCounts = {needs_us, mine, unassigned, overdue, waiting_customer, snoozed: number; oldest_waiting_at: string | null}`; the list's `next_cursor` stays `string | null`.
- Produces in `inbox-queue.ts`:
  ```ts
  export type QueueTabId = "needs_us" | "mine" | "unassigned" | "overdue" | "waiting" | "snoozed" | "resolved" | "all";
  export type QueueOrder = "activity" | "waiting" | "due" | "priority";
  export interface QueueFilters { state: QueueState[]; assignee: "" | "me" | "none" | string; priorityMin: "" | Priority;
    reason: Reason[]; topic: Topic[]; channel: ChannelFilter; sender: SenderFilter; overdue: boolean; waitingOver: number | null }
  export const TABS: readonly { id: QueueTabId; label: string; testId: string; filters: Partial<QueueFilters>; order: QueueOrder; count: keyof QueueCounts | null }[]
  export function tabFilters(tab: QueueTabId): QueueFilters;                    // the tab's own filters over empty ones
  export function queueListQuery(o: { tenantQuery: string; filters: QueueFilters; order: QueueOrder; q: string }): string  // full path and query; only set values; order always sent; q only with 2+ characters
  export function activeFilterChips(filters: QueueFilters, tab: QueueTabId): { key: string; label: string }[]   // filters beyond the tab's own, in words
  export function waitText(since: string | null, now: number): string          // "2h 10m", "35m", "3d", "just now"
  export function dueState(item: Pick<Conversation, "needs_staff_since" | "due_at">, now: number): "none" | "ok" | "soon" | "overdue"   // soon = last quarter of the window
  export function dueText(item, now): string                                     // "Due in 20m", "Overdue 1h"
  export function rowMeta(item: Conversation, now: number, me: { userId: string | null }): { state: {tone: StatusTone; text: string} | null; due: ...; priority: {text: "Urgent" | "High"} | null; reason: string | null; assignee: {initials: string; text: string} | null }
  export function primaryAction(item: Conversation, me: { userId: string | null; role: Role }): { kind: "take_over" | "take_it" | "take_from" | "resolve" | "none"; name?: string }
  export function snoozePresets(now: Date): { label: string; until: string }[]    // Later today (+3h), Tomorrow 09:00, Next Monday 09:00 in +08:00
  export function actionErrorText(status: number | null, code: string | null, fallback: string): string   // the plain sentences of the plan's Review Focus 2
  export function bulkSummary(results: { id: string; outcome: BulkOutcome }[]): { text: string; failedIds: string[] }
  export function initialsOf(name: string | null): string                        // "AD" for "Aina Demo", "?" for null
  ```
  The exact English sentences are decided in this task from design 14.3, 14.4, 14.6 and `docs/CONSOLE_VOICE.md`; later tasks reuse them.
- Consumes: the API shapes in `docs/CONSOLE_API.md`; `StatusTone`, `Role`, `ChannelFilter`, `SenderFilter` types.

- [ ] **Step 1: Write failing node tests** (`inbox-queue.test.mjs`, load the TS like `inbox-api.test.mjs` does): every tab's filters and default order (Needs us `state=needs_staff` order `due`; Mine `assignee=me` with `needs_staff` and `waiting_customer`, order `due`; Unassigned; Overdue `overdue=true` with `needs_staff`, order `due`; Waiting, Snoozed, Resolved with their state and order `activity`; All none, `activity`); `queueListQuery` for each tab, with search under two characters dropped, a repeated `state`, the tenant query first, `order` always present; `waitText` and `dueText` boundaries (59 s, 1 min, 59 min, 1 h, 23 h 59, 24 h, 3 d, a future `since` gives "just now"); `dueState` at 0, 74, 75 and 100 percent of the window and with null dates; `rowMeta` for every state (a `snoozed` row says "Snoozed until Thu 09:00" in +08:00; `bot` rows have no state label; an assignee with a null display name still yields an assignee entry, text "Someone", initials "?"); `primaryAction` for every case in design 14.4 including tenant staff on an item held by someone else (no "take_from"), an admin on it ("take_from"), an item waiting on the customer and a bot-handled item; `snoozePresets` at 23:30 (Tomorrow is the next day 09:00) and on a Friday afternoon (Next Monday); `actionErrorText` for 409 stale, 409 `held_reply_pending`, 409 `customer_wrote`, 403, 404, 422 `until_in_past`, 422 `until_too_far`, 503, a network failure and an unknown code; `bulkSummary` for all done, mixed outcomes (counts and reasons in words) and all failed.
- [ ] **Step 2: Run** `bash <wrapper> node --test W/tests/inbox-queue.test.mjs`; expect failure (module missing).
- [ ] **Step 3: Implement** the types and the module; add both files to `check-isolated.mjs` (source list, test list, node-test runner list as `lib/overview-home.ts` appears).
- [ ] **Step 4: Run** the node test, `inbox-api.test.mjs`, typecheck and eslint on the changed files; expect pass.
- [ ] **Step 5: Commit** `feat(console): the Inbox queue model, pure and tested`.

### Task 2: The data layer

**Files:**
- Create: `W/components/console/inbox/useQueueData.ts` (assignees, saved views, settings), `W/components/console/inbox/useQueueActions.ts`
- Modify: `W/lib/inbox-api.ts` (paths and request builders), `W/components/console/inbox/useInboxActions.ts` (expose the shared `revision`/`refresh`; no behaviour change), `W/tests/inbox-api.test.mjs`, `W/scripts/check-isolated.mjs`, `W/tests/theme-literals.test.mjs`

**Interfaces:**
- Produces in `inbox-api.ts` (pure): `queueActionPath(base, action, query)`, `bulkPath(query)`, `queueActionBody(action, o)` (request_key from `crypto.randomUUID()`, `expected_version`, the action's field), `bulkBody(...)`, paths for `assignees`, `settings`, `views`, and the wire types `InboxAssignee`, `InboxSettings`, `SavedView`, `BulkOutcome`, `QueueActionResult`.
- Produces hooks: `useAssignees(query, enabled)` (fetched once when first needed, 503 gives `{unavailable: true}` not an empty list); `useSavedViews(query)` (list, `create(name, filters)`, `rename`, `replace`, `remove`, errors in plain words, 409 name taken and 409 limit); `useInboxSettings(query)` (read, `save(settings)` with the version, 409 `version_conflict` refreshes); `useQueueActions(query, actions: InboxActions)` returning `run(action, item, fields)` and `bulk(action, ids, fields, seenAt)` that call the routes, reuse `actions`' busy/notice/error/revision plumbing and map every failure with `actionErrorText`; a stale version, 403, 404 and 409s refresh (`revision` bump) and keep the pending request only for uncertain outcomes (network) as `useInboxActions.submit` does.
- Consumes: Task 1.

- [ ] **Step 1: Write failing node tests** for the pure builders (paths with and without `client_id`, bodies per action, a fresh `request_key` each time, `bulkBody` carries `seen_at`, `snooze` carries `until` and `seen_customer_at`).
- [ ] **Step 2: Run** `node --test W/tests/inbox-api.test.mjs`; expect failure.
- [ ] **Step 3: Implement** the builders, types and hooks (the hooks are exercised by the browser tests of Tasks 5 to 8; keep them small and typed). Hooks hold no copy of server data beyond what they fetched and never invent a value on failure.
- [ ] **Step 4: Run** node tests, typecheck, eslint; expect pass.
- [ ] **Step 5: Commit** `feat(console): hooks for the Inbox queue routes`.

### Task 3: The queue bar and the list state

**Files:**
- Create: `W/components/console/inbox/QueueBar.tsx` (tab strip, search, Filters button, Views button slot, Sort, filter chips), `W/components/console/inbox/FiltersPanel.tsx`
- Modify: `W/components/console/inbox/Inbox.tsx`, `W/components/console/inbox/ConversationList.tsx`, `W/scripts/check-isolated.mjs`, `W/tests/theme-literals.test.mjs`
- Test: `tests/test_inbox_queue_browser.py` (new, same stubbed harness as `tests/test_inbox_redesign_browser.py`; import its helpers rather than copy them), `tests/test_inbox_redesign_browser.py` (existing tests updated where this task removes their subject)

**Interfaces:**
- Consumes: `TABS`, `tabFilters`, `queueListQuery`, `activeFilterChips`, `QueueCounts`.
- Produces: `QueueBar({ tab, onTab, counts, countsOk, search, onSearch, filters, onFilters, order, onOrder, chips, onRemoveChip, onClear, viewsSlot, canChooseAssignee... })`. Inbox state becomes `tab`, `filters`, `order`, `search`; choosing a tab sets its filters and default order; the list path comes from `queueListQuery`. Layout per design 14.2: one bar above the three panes (tab strip with counts, then search, Filters, Views, Sort), sticky; the list pane keeps only rows, its states and "Load older"; the old chips, channel select and "Conversations" heading are removed (the list pane keeps an off-screen heading with `data-conversation-list-heading` for focus return). The tab strip is a `tablist` (arrow keys move, Enter or Space selects, `aria-selected`, each tab named "Needs us, 12"); the counts come from `extra.counts` of the list response and show "–" until the first answer and when the request failed; the existing nudge animation moves to the Needs us count (`data-testid="inbox-needs-us-count"`) and runs only when the count rises and motion is allowed. Filters popover (a sheet below 768 px) with Assigned to, Priority, Reason, Topic, Channel, Sender, Overdue only, Waiting longer than; applied filters show as removable chips each named "Remove filter: {label}" and "Clear filters". Sort dropdown (Oldest waiting, Due soonest, Newest activity, Priority; Priority only in the Needs us tab). Below 768 px the strip becomes a scrollable row of chips inside a labelled region and Filters and Views open full-width sheets. The assignee filter offers "Anyone", "Me", "Unassigned" and each person from `useAssignees` (plain staff see the same list).
- The `needs_review` request parameter is no longer sent; "held reply" is the Reason filter. `inbox-needs-review-count`, `inbox-filter-*` and `inbox-view` testids are removed deliberately: update their tests in the same task.

- [ ] **Step 1: Write failing browser tests** in `test_inbox_queue_browser.py` (stub rows carry the queue fields and `counts`; the stub records each list request): the bar shows eight tabs with counts from the stub; choosing each tab sends exactly its filters and `order`; a count request that fails shows "–" and not 0; Needs us nudges only when its count rises; search sends under the same debounce and ignores one character; Filters opens, selecting Priority and Assigned to me sends `priority_min=high` and `assignee=me`, chips appear with removable names, Clear filters resets to the tab's own filters; Sort sends `order`, Priority sort is absent outside Needs us; arrow keys move between tabs; at 390 px the strip scrolls inside its own region and Filters is a sheet that Escape closes and returns focus from; no horizontal page overflow at 320 and 1440. Update `tests/test_inbox_redesign_browser.py` where it asserted the removed chips and needs-review count (`test_needs_review_count_stays_visible_and_filters_on_the_server`, `test_inbox_view_chips_filter_like_the_old_control`, the row marker test only if it breaks): rewrite them for the new controls rather than deleting their intent.
- [ ] **Step 2: Run** `bash <wrapper> python -m pytest tests/test_inbox_queue_browser.py -q`; expect the new tests to fail.
- [ ] **Step 3: Implement** the bar, the panel and the Inbox state; keep J and K, search clear, the slide-over, back-on-phone and the empty-state connect button working.
- [ ] **Step 4: Run** `tests/test_inbox_queue_browser.py`, then `tests/test_inbox_redesign_browser.py` in full, then every other browser file that renders the Inbox (the grep in Global Constraints) one at a time; node tests, typecheck, eslint, `theme-literals`, isolated check; expect pass.
- [ ] **Step 5: Commit** `feat(console): the Inbox queue bar with tabs, counts, filters and sort`.

### Task 4: The conversation row

**Files:**
- Create: `W/components/console/inbox/ConversationRow.tsx`
- Modify: `W/components/console/inbox/ConversationList.tsx`, `W/scripts/check-isolated.mjs`, `W/tests/theme-literals.test.mjs`
- Test: `tests/test_inbox_queue_browser.py`, `tests/test_inbox_redesign_browser.py`

**Interfaces:**
- Consumes: `rowMeta`, `waitText`, `dueText`, `initialsOf`, `CustomerAvatar`, `StatusLabel`.
- Produces `ConversationRow({ item, selected, disabled, now, tab, me, selectMode, checked, onCheck, onSelect })`: three lines per design 14.3: name and clock (wait time in the Needs us, Mine, Unassigned and Overdue tabs; last message time in the others; absolute time in `title`; `inbox-row-time`), preview (`inbox-row-preview`), meta line with only what applies (state `StatusLabel`, due label, High or Urgent priority with icon and word, reason in plain words, assignee chip with 20 px initials and "You" or the first name, and the dashed "New sender" label; no pill for known senders; the channel as a small icon with a hidden name and `data-testid="inbox-channel-badge"`). Heavier name weight when the newest message is the customer's and unanswered (`last_customer_at` after `last_staff_reply_at`). A real checkbox named "Select {name}" at the left appears on hover, on focus and whenever any row is checked; `selectMode` (touch devices, toggled from the bar in Task 6) shows it always. The row stays one `button` for opening plus a sibling checkbox (no nested interactive elements). Memoised by id and `inbox_version`.

- [ ] **Step 1: Write failing browser tests**: a row of each state shows the right labels and nothing else (Needs us escalation: state, due, High, "Escalated"; a bot row shows no state; a snoozed row shows "Snoozed until …"); an overdue row says "Overdue" in words; an item claimed with a null `assignee.display_name` still shows an assignee chip; the wait time comes from `needs_staff_since` (stub a fixed `now` through the browser clock) and the other tabs show the last message time; known senders show no sender pill and a new sender shows the dashed label; unread emphasis follows `last_customer_at`; the checkbox appears on focus and is named; a long name does not break the row (the existing long-name test keeps passing); no overflow at 320.
- [ ] **Step 2: Run** the new tests; expect failure.
- [ ] **Step 3: Implement** the row and use it in the list (J and K and the selection ring keep working).
- [ ] **Step 4: Run** `tests/test_inbox_queue_browser.py`, `tests/test_inbox_redesign_browser.py` (update `test_rows_show_preview_time_and_review_marker` and `test_unnamed_whatsapp_sender...` only where the design changed their subject), node tests, typecheck, eslint, theme-literals.
- [ ] **Step 5: Commit** `feat(console): three-line conversation rows with state, wait, priority and assignee`.

### Task 5: The thread header and item actions

**Files:**
- Create: `W/components/console/inbox/ThreadActions.tsx` (primary action, Snooze menu, More menu), `W/components/console/inbox/QueueBanner.tsx`, `W/components/console/inbox/QueueDialogs.tsx` (assign, priority, topic, custom snooze time)
- Modify: `W/components/console/inbox/Thread.tsx`, `W/components/console/inbox/OpenConversation.tsx`, `W/scripts/check-isolated.mjs`, `W/tests/theme-literals.test.mjs`
- Test: `tests/test_inbox_queue_browser.py`, `tests/test_inbox_redesign_browser.py`

**Interfaces:**
- Consumes: `primaryAction`, `snoozePresets`, `useQueueActions`, `useAssignees`, `actionErrorText`, `QueueCounts` untouched.
- Produces: the thread header shows the state and due labels beside the customer line and the actions of design 14.4: one primary action by `primaryAction` ("Take over" for a bot-handled chat exactly as today; "Take it" claims; "Take it from {name}" admins only; "Resolve" for items claimed by you or waiting or snoozed), a Snooze menu (the presets and "Pick a time", native datetime field, aware time, in the company timezone), and a More menu (Assign to… for admins only, Give up this item, Set priority, Set topic, Hand back to the assistant). Resolve and Snooze send the loaded item's `last_customer_at` as `seen_customer_at`. The banner shows "Waiting on us for 3h 20m, due 1h ago", "A held reply needs a decision first" after a `held_reply_pending` refusal, and "The customer wrote after you opened this. Reload to see it." after `customer_wrote`. Takeover, release (confirm dialog kept), the reply form with its two-step confirmation, held-reply cards and the customer card are unchanged. After every outcome the item refreshes (`revision` bump) and the notice says what happened in plain words. Menus and dialogs are native dialogs or `SlideOver`, return focus to their button and close on Escape.

- [ ] **Step 1: Write failing browser tests** (stub the queue routes and record bodies): for each item kind the primary action is the right one and a click sends the right route and body (claim with `expected_version`; resolve with `seen_customer_at`); a tenant staff session never sees Assign or "Take it from {name}" and an admin session does; Snooze presets send an aware `until` (assert offset +08:00) and "Pick a time" validates future times in words; each of 409 `held_reply_pending`, 409 `customer_wrote`, a stale version, 403 and 503 shows its plain sentence and refreshes the item; Set priority and Set topic send their body; Assign lists the stub's assignees, shows "member directory unavailable" on 503 and never an empty list; the banner texts; Take over, Hand back (confirm), the reply confirmation and a held-reply approval still work (reuse the existing tests, which must keep passing); Escape closes each menu and returns focus; no overflow at 320 and 390.
- [ ] **Step 2: Run** the new tests; expect failure.
- [ ] **Step 3: Implement** the header, menus, dialogs and banner.
- [ ] **Step 4: Run** `tests/test_inbox_queue_browser.py`, `tests/test_inbox_redesign_browser.py`, every other browser file that renders the Inbox, one at a time; node tests, typecheck, eslint, theme-literals; expect pass.
- [ ] **Step 5: Commit** `feat(console): claim, snooze, resolve, assign, priority and topic from the thread`.

### Task 6: Selection and bulk actions

**Files:**
- Create: `W/components/console/inbox/BulkBar.tsx`
- Modify: `W/components/console/inbox/Inbox.tsx`, `W/components/console/inbox/QueueBar.tsx`, `W/components/console/inbox/ConversationList.tsx`, `W/scripts/check-isolated.mjs`, `W/tests/theme-literals.test.mjs`
- Test: `tests/test_inbox_queue_browser.py`

**Interfaces:**
- Consumes: `useQueueActions.bulk`, `bulkSummary`, `ConversationPage.as_of`, `ConversationRow`'s checkbox.
- Produces: selection state in `Inbox` (a `Set` of ids, cleared on tab or filter change, capped at 50 with a plain message at the cap, "Select" mode toggle in the queue bar for touch devices); when anything is selected the bar's second line becomes the bulk bar: "N selected", Claim, Assign (admins only), Snooze (same presets menu), Resolve, Clear. The request sends the list's latest `as_of` as `seen_at`. The result is one summary line from `bulkSummary` and a list of what did not apply and why in words; failed items stay selected, done ones are removed from the selection; focus moves to the summary. Select all selects the loaded page only and says so.

- [ ] **Step 1: Write failing browser tests**: checking rows shows the bulk bar with the count; the request body has `action`, the ids, `seen_at` equal to the stub's `as_of` and no per-item version; a stub result of 28 done, one `customer_wrote` and one `held_reply_pending` shows the exact words, keeps those two selected and drops the rest; the 51st row cannot be checked and the message says why; tenant staff see no Assign; tab change clears the selection; Escape clears it; keyboard: Space toggles the focused checkbox; the touch "Select" mode shows the checkboxes; no overflow at 390.
- [ ] **Step 2: Run** `tests/test_inbox_queue_browser.py`; expect the new tests to fail.
- [ ] **Step 3: Implement** the selection model and the bulk bar.
- [ ] **Step 4: Run** `tests/test_inbox_queue_browser.py` and `tests/test_inbox_redesign_browser.py`; node tests, typecheck, eslint, theme-literals; expect pass.
- [ ] **Step 5: Commit** `feat(console): select conversations and act on them together`.

### Task 7: Saved views

**Files:**
- Create: `W/components/console/inbox/ViewsMenu.tsx`
- Modify: `W/components/console/inbox/QueueBar.tsx`, `W/components/console/inbox/Inbox.tsx`, `W/scripts/check-isolated.mjs`, `W/tests/theme-literals.test.mjs`
- Test: `tests/test_inbox_queue_browser.py`

**Interfaces:**
- Consumes: `useSavedViews`, `tabFilters`, `QueueFilters`.
- Produces: the Views menu per design 14.5: lists the person's saved views (choosing one sets tab-equivalent filters, `order` and clears search; a saved view is stored as the API's `filters` object with `order`, never `q`), "Save this view" (name field, 60 characters, shows the 409 name-taken and limit-of-20 sentences in words), "Manage views" (rename, delete with a confirm). A view whose stored filters the UI cannot express (an unknown key) is listed disabled with the words "This view uses a filter this screen does not have yet". The menu shows nothing until the list has loaded and never an empty "no views" state while loading.

- [ ] **Step 1: Write failing browser tests**: saving the current filters posts `name` and the filters object (no `q`, with `order`); choosing a view sends exactly those filters on the next list request; rename and delete call their routes; the duplicate-name and 20-view errors show their plain words; another person's views are never shown (stub per user); the unknown-filter view is disabled; Escape closes and returns focus.
- [ ] **Step 2: Run** the new tests; expect failure.
- [ ] **Step 3: Implement** the menu.
- [ ] **Step 4: Run** `tests/test_inbox_queue_browser.py`; node tests, typecheck, eslint, theme-literals; expect pass.
- [ ] **Step 5: Commit** `feat(console): saved views in the Inbox queue bar`.

### Task 8: Queue settings

**Files:**
- Create: `W/components/console/inbox/QueueSettings.tsx`
- Modify: `W/components/console/inbox/QueueBar.tsx`, `W/scripts/check-isolated.mjs`, `W/tests/theme-literals.test.mjs`, `W/lib/inbox-api.ts`
- Test: `tests/test_inbox_queue_browser.py`, `W/tests/inbox-api.test.mjs`

**Interfaces:**
- Consumes: `useInboxSettings`.
- Produces: a "Response times" button in the queue bar opening a `SlideOver` with the four response targets by priority (minutes, shown as hours and minutes while typing is in minutes), the plain statement that a change affects only items that enter the queue afterwards, and the five fields the reminders will use shown read-only with "Used by reminders, which come later" (they are saved unchanged: the PUT must carry every field). Tenant admins and platform admins can edit and save; tenant staff see it read-only with the sentence "Only administrators can change these". Client-side ranges equal the API's (5 to 10080 and so on); a 409 `version_conflict` reloads the form with a notice; the save button is disabled while unchanged or invalid; errors are plain words.

- [ ] **Step 1: Write failing tests**: node test for the range validation and the PUT body (every field plus `version`); browser tests: admin edits `target_normal_min` to 30 and the PUT body carries all nine fields and the read `version`; invalid values are refused in words before sending; staff see read-only with the sentence and no save button; a 409 conflict shows the reloaded values; Escape closes and returns focus.
- [ ] **Step 2: Run** the node test and the browser file; expect failure.
- [ ] **Step 3: Implement** the panel.
- [ ] **Step 4: Run** both again plus typecheck, eslint, theme-literals; expect pass.
- [ ] **Step 5: Commit** `feat(console): the company's response times in the Inbox queue bar`.

### Task 9: Keyboard, empty states and polish

**Files:**
- Modify: `W/lib/inbox-shortcuts.ts`, `W/tests/inbox-shortcuts.test.mjs`, `W/components/console/inbox/Inbox.tsx`, `W/components/console/inbox/ConversationList.tsx`, `W/lib/inbox-queue.ts` (empty-state copy), `W/components/console/inbox/ShortcutHelp.tsx` (new), `W/scripts/check-isolated.mjs`, `W/tests/theme-literals.test.mjs`
- Test: `tests/test_inbox_queue_browser.py`, `tests/test_inbox_redesign_browser.py`

**Interfaces:**
- Produces: shortcuts C (claim), E (resolve), S (open the snooze menu) and `/` (focus search) added to the existing J, K and Escape through `shortcutFor`, active only when focus is not in a field or dialog, never while an action is pending, each also available as a visible button, and a "?" help dialog listing them; per-tab empty states with the sentences of design 14.7 ("Nothing needs your team right now" and the rest; the resting assistant avatar at 48 px beside the sentence in the Needs us tab only); loading and failed states keep the shared `ListState`; windowing of rows beyond 200 loaded rows is NOT built (note the limit in the PR); `aria-live` politeness for count changes is rate limited (one announcement per change burst).

- [ ] **Step 1: Write failing tests**: node tests for `shortcutFor` with the new keys (ignored in inputs, textareas, selects, dialogs and with modifiers) in `inbox-shortcuts.test.mjs`; browser tests: C claims the open item, E resolves it (with its refusal sentences), S opens the Snooze menu, `/` focuses search, "?" opens the help and Escape closes it, none of them fire while typing a reply; each tab's empty state shows its sentence; the existing J and K test keeps passing.
- [ ] **Step 2: Run** the node and browser tests; expect failure.
- [ ] **Step 3: Implement** the shortcuts, help and empty states.
- [ ] **Step 4: Run** `tests/test_inbox_queue_browser.py`, `tests/test_inbox_redesign_browser.py`, node tests, typecheck, eslint, theme-literals; expect pass. Also run the five-widths-in-three-themes test.
- [ ] **Step 5: Commit** `feat(console): Inbox shortcuts, tab empty states and polish`.

### Task 10: Other browser files, packaged acceptance, docs and whole-branch checks

**Files:**
- Modify: every browser file that drives the Inbox (found by the grep in Global Constraints), `tests/test_packaged_console.py` (the inbox acceptance flow), `docs/INBOX_RETEST.md` (UI rows), `docs/PROGRESS.md`, `W/README.md` or `CSA_WebConsole/csa-console/STATE.md` if they describe the Inbox page

- [ ] **Step 1: Find and fix** every other browser test, fixture or the packaged acceptance flow that selects the old chips, the channel select, the "Conversations" heading, the old row pills or the old needs-review count; update them to the new controls (keep intent), and run each one at a time. The packaged acceptance test is slow: run it once in the background and wait.
- [ ] **Step 2: Docs**: `docs/INBOX_RETEST.md` gets UI rows for the owner (marked not yet run): tabs and counts, a row's labels, claim, snooze and resolve from the thread, bulk resolve with a refused item, saved view, response times as admin and as staff, shortcuts, the three themes and 390 px; `docs/PROGRESS.md` gets a short entry (front end only, no migration, no route change, no customer-facing change, the screen design in section 14).
- [ ] **Step 3: Checks**: all changed and added browser files one at a time, `node --test` for every changed web test, typecheck, `node node_modules/.bin/eslint` on the changed files, the theme-literals and isolated checks (`check:isolated` stops at the pdfjs typecheck on this host: say so), `git diff origin/main --stat -- db csa docs/console_openapi.json` is empty.
- [ ] **Step 4: Commit** `docs: Inbox queue screen retest rows and progress`.
- [ ] **Step 5: Whole-branch review** on the most capable model, one fix wave, one scoped re-review; merge main; push; open the PR with `gh api -X POST repos/amsyrzmi/CSAV2/pulls --input <json>` (body: what changed on the screen, no backend change, no rollout, the known limits such as no row windowing beyond 200 loaded rows, the rulings); watch CI to green and then the post-merge main run; tell the owner the PR number. No rollout by this session unless the owner asks.
