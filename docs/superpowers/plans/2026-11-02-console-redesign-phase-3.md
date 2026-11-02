# Console Redesign Phase 3 (phases A, B, C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the console calmer for non-technical people: shared building blocks, avatars without a box that suit every theme, an e-invoicing avatar, a simpler and more interactive chat, and plain-first redesigns of Assistant activity, Knowledge base, Business management, Customer inbox, Email assistant, Subscriptions and Team. Phase D (People & HR ideas 2 to 5 and the restyle of the HR screens) is ON HOLD and is not in this plan.

**Architecture:** Four small shared pieces in `WEB/components/console/kit/` (details row, status chips, page intro, calendar date-range filter, with its pure logic in `WEB/lib/date-range.ts`) are built and tested first; every tab then composes them. Two additive backend changes (run filters, order filters plus a counts read) keep today's defaults. Existing visible wording is not rewritten: new elements carry new plain text and original strings stay verbatim inside the opened details.

**Tech Stack:** Next.js 16.3.8 (read `node_modules/next/dist/docs/` before touching layouts), React 19, Tailwind 4, `lucide-react`, node `--test`, Playwright (python) stubbed-upstream browser tests, FastAPI + psycopg SQL files under `db/sql/`.

**Spec:** `docs/superpowers/specs/2026-11-02-console-redesign-phase-3-design.md` (binding; read it and section 9b first). Facts behind it: `docs/superpowers/specs/2026-11-02-console-redesign-phase-3-facts/` (four fact sheets with file:line references; read the one for your screen). Earlier binding specs: `2026-10-29-console-redesign-phase-2-design.md`, `2026-10-30-console-redesign-phase-2b-design.md`.

## Global Constraints

- Web root `CSA_WebConsole/csa-console/web` (WEB). Run every repo test or lint command through the wrapper `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/44845bab-014a-4610-95de-184b6a6d4038/scratchpad/ci.sh <command>` (no execute bit; worktree sessions refuse `VAR=value` prefixes and compound commands). Node: `/tmp/claude-1000/-home-amsyrzmi-CSAV2/44845bab-014a-4610-95de-184b6a6d4038/scratchpad/bin/node`. Python tests: `bash …/ci.sh python -m pytest <file> -q`. Typecheck needs the API origin variable: `bash …/scratchpad/ciweb.sh …/bin/node node_modules/.bin/next typegen` then `… node_modules/.bin/tsc --noEmit` from WEB. ESLint: `… node_modules/.bin/eslint`. Python scripts that import `csa` must run with the worktree on the path (`python -c "import sys,runpy; sys.path.insert(0,'.'); runpy.run_path('scripts/<name>.py', run_name='__main__')"`), otherwise the main checkout's package is imported.
- Host: 14 GB shared, little free memory. One heavy thing at a time (browser suite, build, typegen); check `free -h` first; run only the changed files' tests; CI is the merge gate. Long commands: `nohup` in the background and poll with an until-loop, never `sleep` chains.
- Keep every existing `data-testid` stable. New `lib/` and test files go into `scripts/check-isolated.mjs` (staged list AND commands list); `components/console/*` is already staged wholesale. Tokens only in owned files (`tests/theme-literals.test.mjs`; add new owned files to its OWNED list). No fake figures; a failed load never shows as zero.
- **No rewrite of existing visible wording** (owner: "do not fix any labelling yet"): nav names, existing labels, buttons and statuses stay exactly. New elements carry new plain text under `docs/WRITING_GUIDE.md`. Original strings (for example "Succeeded", `#id agent`, `MYR`, raw codes) stay verbatim inside the opened details; tests that grep them open the arrow first.
- Backend: additive only (optional query parameters, one new read, defaults unchanged); SQL only in `db/sql/`; Pydantic contracts; contract tests; regenerate `docs/console_openapi.json` and update `docs/CONSOLE_API.md`. Do not change anything under `csa/hr`, `csa/email`, `csa/workspace`. Do not edit `docs/PROGRESS.md` or HR files (`components/console/hr/**`, `HrAccess*.tsx`). `EXPECTED_SCHEMA_VERSION` stays "0520". A migration (0503) only where Task 15 says.
- The sandbox chat answers knowledge and product-information questions only; no copy may promise more; chips and follow-up starters only prefill the composer.
- Animation never stands for state: a badge and words always carry it; honour reduced motion, offscreen suspension and the "Reduce animations" preference; the rest pose is idle frame 0.
- Five widths (320, 390, 768, 1024, 1440) without horizontal overflow in cobalt, night and bureau; 4.5:1 text and 3:1 non-text contrast in every theme; keyboard operable with visible focus.
- Commit locally after each task; message ends with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` exactly. Never push, rebase or amend earlier commits. Next.js 16 differs from your training data: read the relevant guide in `WEB/node_modules/next/dist/docs/` before touching layouts, routes or `loading.tsx`.
- Shorthand: `S=/tmp/claude-1000/-home-amsyrzmi-CSAV2/44845bab-014a-4610-95de-184b6a6d4038/scratchpad`.

## Review Focus

- Date filters: a range of one day, start after end, month boundaries, the last second of a day. The browser sends local-day instants (`from` inclusive, `to` exclusive) and the server returns rows of exactly that window; the keyset cursor still pages correctly with a filter on (Tasks 2, 10, 11, 14, 16).
- Open details survive the 5-second refresh (state keyed by run id, not by array index) and paging does not jump (Task 11).
- The last-five strip never says "working now", shows fewer than five when fewer agents have run, and de-duplicates by avatar (Task 11).
- Avatars: no element anywhere keeps a background, radius or clip; every avatar edge reaches 3:1 on night with the halo; reduced motion shows the rest pose (Tasks 3, 4).
- Confirmation steps cannot be skipped by Enter, double click or Escape, and focus returns to the trigger (Tasks 13, 14, 18).
- Chat paths unchanged: draft kept on failed create, uncertain-send retry, held/queued/failed wording, read-only role, recorded voice consent (Tasks 7, 8).
- Business: status chip counts ignore the status filter itself; orders with an empty placed date still list; RM formatting; `identity-transport.test.mjs` source assertions (Tasks 15, 16).

---

## Phase A: shared pieces and quick fixes

### Task 1: Details row, status chips, page intro

**Files:** Create `WEB/components/console/kit/DetailsRow.tsx`, `StatusChips.tsx`, `PageIntro.tsx`, `index.ts`, `WEB/lib/chips.ts`; Test `WEB/tests/chips.test.mjs`, `tests/test_kit_browser.py`; Modify `WEB/scripts/check-isolated.mjs`, `WEB/tests/theme-literals.test.mjs` (OWNED).

**Interfaces (Produces):**
- `DetailsRow({ summary: ReactNode; children: ReactNode; open?: boolean; defaultOpen?: boolean; onOpenChange?: (open: boolean) => void; testId?: string; toggleLabel?: string })`: summary on the left, a chevron `<button aria-expanded aria-controls data-testid={`${testId}-toggle`}>` with visible text "Show details" / "Hide details", the details region `id`-linked, `data-testid={testId}`. Controlled when `open` is given. Links and buttons inside `summary` stay clickable (the whole summary is not the toggle). No height animation under reduced motion.
- `StatusChips<T extends string>({ options: { value: T; label: string; count?: number | null }[]; value: T | T[]; onChange: (next: T | T[]) => void; mode: "single" | "multi"; testId: string; ariaLabel: string })`: a `role="group"` of buttons with `aria-pressed`, per-chip `data-testid={`${testId}-${value}`}`, a check mark glyph plus bold text when pressed (not colour alone), count shown as `label (count)` only when `count` is a finite number >= 0 (null or undefined shows no number); wraps on narrow screens.
- `lib/chips.ts`: `toggleChip<T>(current: T[], value: T): T[]` (multi), `nextSingle<T>(current: T, value: T): T`.
- `PageIntro({ children })`: `<p data-testid="page-intro">` in muted text, max two lines at 320 px.

- [ ] **Step 1: Write failing tests.** Node `chips.test.mjs`: `toggleChip adds and removes`, `nextSingle returns the pressed value`, `count shows only for finite non-negative numbers` (if exposed as `chipLabel(option)` from `lib/chips.ts`). Browser `tests/test_kit_browser.py` stages a temporary page `app/kit-demo/page.tsx` into its staged copy only (never committed; the test also adds `/kit-demo` to the staged `proxy.ts` allowlist) that renders all kit pieces; tests: `test_details_row_opens_with_keyboard_and_reports_aria_expanded`, `test_details_row_keeps_links_inside_summary_clickable`, `test_status_chips_single_and_multi_press_state`, `test_kit_pieces_do_not_overflow_at_320_in_three_themes`, `test_kit_contrast_in_three_themes` (text 4.5:1, chip border and focus ring 3:1).
- [ ] **Step 2: Run** `bash $S/ci.sh $S/bin/node --test tests/chips.test.mjs` and the browser file; expect FAIL.
- [ ] **Step 3: Implement** the three components and `lib/chips.ts` with theme tokens only; export from `kit/index.ts`.
- [ ] **Step 4: Run** the node and browser tests, eslint, typecheck.
- [ ] **Step 5: Commit** `feat(console): shared details row, status chips and page intro`.

### Task 2: Calendar date-range filter

**Files:** Create `WEB/lib/date-range.ts`, `WEB/components/console/kit/DateRangeFilter.tsx`; Modify `kit/index.ts`; Test `WEB/tests/date-range.test.mjs`, extend `tests/test_kit_browser.py`; Modify `check-isolated.mjs`, theme-literals OWNED.

**Interfaces (Produces):** `type DayRange = { from: string | null; to: string | null }` (local calendar dates `YYYY-MM-DD`, inclusive both ends). In `lib/date-range.ts`:
- `monthGrid(year: number, month: number, weekStart?: 0 | 1): (string | null)[][]` (rows of 7, `null` padding, default week starts Monday).
- `presets(today: string): { key: "today" | "last7" | "last30" | "month" | "clear"; label: string; range: DayRange }[]` ("Today", "Last 7 days" (today and the six days before), "Last 30 days", "This month", "Clear").
- `selectDay(range: DayRange, day: string): DayRange` (first click sets both ends to the day; second click sets the end, swapping if earlier than the start; a click when both ends differ restarts at that day).
- `toInstants(range: DayRange, now?: Date): { from: string | null; to: string | null }`: ISO UTC strings; `from` is the local start of `range.from`; `to` is the local start of the day AFTER `range.to` (exclusive). Null stays null. Uses the browser time zone only.
- `describeRange(range: DayRange): string` ("Any date", "12 Nov 2026", "12 Nov to 18 Nov 2026").
- `DateRangeFilter({ value: DayRange; onChange: (r: DayRange) => void; testId?: string; label?: string; today?: string })`: a button showing `describeRange`, opening a popover (full-width bottom sheet below 640 px) with the month grid, previous and next month buttons, the preset buttons, today marked, the selected range highlighted (text plus an outline, not colour alone), a "Done" button, Escape closes and returns focus to the button; arrow keys move the focused day, Enter selects, PageUp/PageDown change month; `role="grid"` with `aria-label` per month; `data-testid={testId}` on the button and `${testId}-popover` on the popover; the selected days carry `aria-selected`.

- [ ] **Step 1: Write failing tests.** Node: `monthGrid has 6 or fewer rows of 7 and Monday first`, `monthGrid February 2028 (leap) holds 29 days`, `presets last7 spans exactly 7 days ending today`, `selectDay first click sets a one-day range`, `selectDay second earlier click swaps ends`, `selectDay third click restarts`, `toInstants makes to exclusive by one day and keeps null`, `toInstants at a month boundary (31 Oct to 1 Nov) spans the right instants`, `describeRange wording`. Browser: `test_date_filter_selects_a_range_by_clicks_and_presets`, `test_date_filter_keyboard_and_escape_focus_return`, `test_date_filter_sheet_fits_at_320_in_three_themes`, `test_selected_range_visible_without_colour` (outline or text marker present).
- [ ] **Step 2: Run** the node file and the new browser tests; expect FAIL.
- [ ] **Step 3: Implement** the logic first (pure, no `Date` parsing of non-ISO strings; construct dates from parts), then the component (no new dependency).
- [ ] **Step 4: Run** both, eslint, typecheck, theme-literals test.
- [ ] **Step 5: Commit** `feat(console): calendar date-range filter`.

### Task 3: Avatars without a box, readable on every theme

**Files:** Modify `WEB/components/console/fs/AgentAvatar.tsx` (remove tile background, radius, clip), `WEB/app/themes.css` (add `--fs-sprite-halo` per theme; remove `--fs-sprite-tile` once unused), `WEB/components/console/ServicesWorkspace.tsx` and `WEB/components/console/RuntimeWorkspace.tsx` (remove the `CHIP` wrapper boxes around the avatar), `WEB/components/console/chat/ChatEmptyState.tsx` and `OverviewHome.tsx` (`AssistantTile` loses its box), every other place that renders `AgentAvatar` (7 places; grep) only if they add their own box; Test `WEB/tests/pixel-avatars.test.mjs` and `WEB/tests/theme-contrast.test.mjs` (replace the tile assertions), `tests/test_agents_card_browser.py` and `tests/test_services_redesign_browser.py` (extend).

**Interfaces:** `AgentAvatar` props unchanged; the root span has no background, no border radius, no overflow clip. Halo: `filter: drop-shadow(Npx 0 0 var(--fs-sprite-halo)) drop-shadow(-Npx 0 0 …) drop-shadow(0 Npx 0 …) drop-shadow(0 -Npx 0 …)` where N = `size / grid` px (one sprite cell), crisp; tokens: night = the former tile colour `#dbe6fb`, cobalt and bureau = `transparent`. The halo is not drawn in the dim state.

- [ ] **Step 1: Write failing tests.** Node: `every avatar's silhouette edge cells reach 3:1 against each theme surface counting the halo (night) or the sprite alone (cobalt, bureau)` (compute from `lib/pixel-avatars-data.ts` and theme tokens), `--fs-sprite-halo is defined in all three themes`, `no --fs-sprite-tile reference remains`. Browser: `test_no_avatar_has_a_background_or_radius` (every `[data-agent]` element and its parents up to the card: `getComputedStyle` background is transparent and radius 0), `test_services_row_avatar_has_no_second_chip`, `test_avatar_halo_present_on_night_only`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement.** If an edge cell fails 3:1 on night even with the halo (the halo is the light colour so it should pass), report instead of changing sprite art.
- [ ] **Step 4: Run** `tests/pixel-avatars.test.mjs`, `tests/theme-contrast.test.mjs`, `tests/theme-literals.test.mjs`, the two browser files, eslint, typecheck.
- [ ] **Step 5: Commit** `feat(console): avatars drawn without a box, with a light outline on the night theme`.

### Task 4: E-invoicing avatar

**Files:** Modify `WEB/lib/agent-avatars.ts` (`AGENT_IDS` gains `"einvoicing"`, `AGENT_TITLES.einvoicing = "E-invoicing agent"`, `CAPABILITY_AVATARS` gains `["lhdn_agent", "einvoicing"]`, the package map gains `e_invoicing`), `scripts/pixelate_avatars.py` (`HUE["einvoicing"]`, an `idle_frames` branch, `PROPS["einvoicing"]`, a `working_plan` branch), a new source SVG in `WEB/public/fourstacks/agents/` and the ui-guide beside the others, regenerated `WEB/lib/pixel-avatars-data.ts`; Test `WEB/tests/agent-avatars.test.mjs`, `public-assets` test, the `HUE` table in `WEB/tests/pixel-avatars-data.test.mjs`, `tests/test_pixelate_avatars.py`, `tests/test_services_redesign_browser.py` and `tests/test_agents_card_browser.py` (flip the cases that assert E-invoicing has no avatar).

**Interfaces:** the new id matches `[a-z]+`; the avatar follows the family (a pixel creature with a document-and-stamp motif: a receipt with a seal, so shape not hue alone identifies it), hue from the largest free arc (about 120; confirm against the existing HUE table and keep the neighbour gap the generic test requires), 32 and 48 grids, four idle frames, eight working frames (stamping), at most 12 palette entries, passes the 3:1 night-edge rule from Task 3. See `facts/subscriptions-team-avatar-chat.md` section 3 for the exact checklist. The generator's Python half (`build_agent`, `idle_frames`, `working_frames`, `render`) runs without Chromium: feed it hand-drawn 32 and 48 grids; do NOT run the Chromium-based `--check` (record in the report that it still needs a manual run).

- [ ] **Step 1: Update tests first**: avatar count 11 becomes 12; the `HUE` table gains `einvoicing`; flip the "no avatar" cases; add `test_einvoicing_avatar_renders_in_services_row_and_agent_card`.
- [ ] **Step 2: Run** and see them fail.
- [ ] **Step 3: Implement** the sprite, maps and generated data module.
- [ ] **Step 4: Run** `tests/agent-avatars.test.mjs`, `tests/pixel-avatars-data.test.mjs`, `tests/pixel-avatars.test.mjs`, public-assets, `tests/test_pixelate_avatars.py`, the two browser files, eslint, typecheck.
- [ ] **Step 5: Commit** `feat(console): e-invoicing avatar`.

### Task 5: Password page polish

**Files:** Modify `WEB/components/console/PasswordPanel.tsx`, `WEB/components/ui/` shared password card only if `/change-password` shares it (check `app/(auth)/change-password`); Test `tests/test_password_browser.py` (extend), `WEB/tests/password-messages.test.mjs` (extend if a pure helper is added), add `WEB/lib/password-rules.ts` if you extract the checklist logic.

**Interfaces:** same endpoint, rules, error messages and test ids as today. Adds, in the same card: a show/hide button per field (`type` toggles, button `aria-pressed`, label names the field); a live checklist under the new password with three rules ("At least 12 characters", "Different from your current password", "Matches the confirmation") that tick as the person types, each item with a check or empty marker AND text (state announced via `aria-live="polite"` on the list container only for the first change after typing pauses); an advisory length-based strength bar that never blocks submit and sends nothing anywhere; after success a panel that says other devices are signed out (the backend revokes all sessions and tokens and issues a new one); the Save button stays disabled until the three rules pass.

- [ ] **Step 1: Write failing tests** `test_password_checklist_ticks_as_you_type`, `test_show_hide_toggles_each_field_independently`, `test_save_disabled_until_rules_pass`, `test_strength_hint_never_blocks_a_valid_password`, `test_success_panel_mentions_other_devices`, plus keep the existing tests; node `password-rules.test.mjs` for the pure rule checks and strength function (empty, short, 12, 20+ characters).
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement.** Do not log or persist the password anywhere; keep the hidden username field from the final fix.
- [ ] **Step 4: Run** `tests/test_password_browser.py`, the node tests, eslint, typecheck.
- [ ] **Step 5: Commit** `feat(console): password page checklist, show/hide and strength hint`.

### Task 6: Checkpoint A: preview refresh

**Files:** scratchpad only (`$S/services_stub.py`, `$S/launch_preview.sh`), no commit unless a defect is found.

- [ ] **Step 1:** Restart the preview stack (stop the old `next dev` and stub, then `launch_preview.sh`) so `http://127.0.0.1:3100/login` runs the current code with the extended stub; warm the pages; keep both bound to 127.0.0.1.
- [ ] **Step 2:** Screenshot and LOOK at: the Services page (avatars without a box) and Assistant activity in cobalt, night and bureau at 390 and 1440; the Password page; a kit demo is not needed. Fix defects in owned files with focused commits.
- [ ] **Step 3:** Report the preview URL, accounts and any residuals. Do not wait for anyone: report and finish; the owner reacts to the preview while later phases run.

---

## Phase B: chat

### Task 7: Simpler chat (remove the limits wall, calmer controls)

**Files:** Modify `WEB/components/console/chat/ChatDisclaimer.tsx` (remove the expander and long text), `ChatEmptyState.tsx`, `ChatComposer.tsx`, `ChatToolbar`/the toolbar section of `SandboxWorkspace.tsx`, `WEB/lib/sandbox-chat.ts` (remove now-unused copy constants; keep `chipsFor`), the voice tray components under `components/console/` (`VoiceConversation.tsx`, `RecordedVoice.tsx`) only for layout; Test `tests/test_sandbox_chat_browser.py`, `tests/test_voice_browser.py`, `tests/test_overview_browser.py`, `WEB/tests/sandbox-chat.test.mjs`.

**Interfaces:** `SandboxWorkspace` keeps its name and props `{ me, chromeless?, tenant? }` (the voice and sandbox browser harnesses mount it directly). Behaviour:
- Remove the "What it cannot do" expander and its text, the repeated "private / knowledge base and products only" sentences in the empty state, and the compact one-liner's link. Keep exactly ONE quiet line under the composer: "Answers come from your knowledge base and products." (`data-testid="chat-honesty-line"`).
- Empty state: the avatar (no box), "How can I help today?", the three starter chips (unchanged).
- "Stop reading aloud" renders only while speech is playing. "Check for new messages" renders only after a failed automatic refresh OR while the 90-second delay notice or the lost-refresh notice is showing (their text points to it).
- One microphone button opens the existing voice tray (closed by default); the tray keeps the consent checkbox, language selects and fine print unchanged; hands-free and recorded modes stay reachable inside it.
- Toolbar keeps New chat, history and archive.
- Held, queued and failed wording, retained request key, uncertain-send retry, draft kept on a failed create, read-only role are untouched.

- [ ] **Step 1: Write failing tests** `test_long_limits_text_and_expander_are_gone` (no "What it cannot do" anywhere; the honesty line present once), `test_stop_reading_aloud_only_while_speaking`, `test_check_for_new_messages_appears_after_a_failed_refresh_and_with_the_delay_notice`, `test_microphone_opens_the_voice_tray_with_consent_inside`, and update the existing assertions that mention the removed text; node: remove tests of deleted constants.
- [ ] **Step 2: Run** the new tests; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_sandbox_chat_browser.py`, `tests/test_voice_browser.py`, `tests/test_overview_browser.py`, node tests, eslint, typecheck.
- [ ] **Step 5: Commit** `feat(console): a simpler chat without the limits wall`.

### Task 8: More interactive chat

**Files:** Modify `ChatComposer.tsx`, `ChatMessages.tsx`, `SandboxWorkspace.tsx`, create `WEB/components/console/chat/WorkingRow.tsx` and `JumpToLatest.tsx`, `WEB/lib/sandbox-chat.ts` (`followUpsFor`, `isWorking`); Test as Task 7 plus `WEB/tests/sandbox-chat.test.mjs`.

**Interfaces (Produces):**
- `isWorking(status: string): boolean` true only while a turn is being worked on (the server statuses meaning pending or processing; NOT queued, NOT awaiting delivery); `followUpsFor(): string[]` returns `["Tell me more about ", "Say that in a shorter way", "Give me an example"]` (final wording yours, plain, inside the sandbox limits; they only prefill the composer, never send).
- `WorkingRow`: a 48 px `AgentAvatar` in the `working` state beside "Working on it" (a pending turn is a server-confirmed state, so the animation is truthful), `data-testid="chat-working"`; absent when not `isWorking`.
- The composer grows with its text up to about 8 lines; Enter sends, Shift+Enter adds a line (as built); new messages ease in (none under reduced motion); a "Jump to latest" pill (`data-testid="chat-jump-latest"`) appears when scrolled up by more than a screen and scrolls to the end; each assistant reply gets a Copy button (`aria-label="Copy reply"`, shows "Copied" for 2 s, writes the plain text only); after the latest assistant reply, follow-up starters appear (`data-testid="chat-followups"`), only when the thread is idle.

- [ ] **Step 1: Write failing tests** `test_working_row_shows_the_working_pose_only_while_processing`, `test_queued_turn_shows_no_working_row`, `test_composer_grows_and_shift_enter_adds_a_line`, `test_jump_to_latest_pill_appears_when_scrolled_up`, `test_copy_reply_puts_the_text_on_the_clipboard`, `test_followups_only_prefill_and_never_send`, `test_reduced_motion_disables_message_easing`; node `isWorking` over every status name in `lib/sandbox-chat.ts`, `followUpsFor` stays inside the forbidden-words check used for chips.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement.** Do not change request-key, polling or retry logic.
- [ ] **Step 4: Run** the three chat browser files and the node tests, eslint, typecheck.
- [ ] **Step 5: Commit** `feat(console): working row, copy, jump to latest and follow-up starters in the chat`.

### Task 9: Checkpoint B: preview refresh

Same as Task 6 for the chat: restart the preview, screenshot and look at the Overview empty state, a conversation, a pending turn (extend the stub with a `processing` turn) and the voice tray at 390 and 1440 in the three themes; fix defects; report URL and residuals. Do not wait: the controller keeps executing and the owner reacts to the preview while later tasks run.

---

## Phase C: the tabs

### Task 10: Backend: filters on the runs list

**Files:** Modify `db/sql/console/list_runs.sql`, `csa/console/runtime.py`, `csa/console/router.py` (query parameters of `GET /console/v1/runs`), `docs/CONSOLE_API.md`, `docs/console_openapi.json` (regenerate with the worktree on the path); Test the existing runtime test file (find the one holding `test_m2_runtime_keyset_pagination_detail_redaction_and_overview`) extended.

**Interfaces:** `GET /console/v1/runs` gains optional `from` and `to` (ISO timestamps with offset; `from` inclusive, `to` exclusive, on `created_at`) and `status` (`ok` | `error`; maps to the stored `status` column values). With none of them the response and ordering are exactly today's (`id DESC`, keyset `before_id`, `next_before_id`). `from >= to` or an unknown `status` gives 422 with a code detail (`invalid_range`, `invalid_status`). The same tenant scoping as today. `GET /console/v1/overview` is unchanged.

- [ ] **Step 1: Write failing contract tests** `test_runs_filter_by_date_window_is_half_open`, `test_runs_filter_by_status`, `test_runs_filters_combine_with_agent_and_keyset_paging` (page through a filtered set and assert no gap or repeat across pages), `test_runs_without_filters_is_unchanged`, `test_runs_rejects_inverted_range_and_unknown_status`, `test_runs_filters_stay_tenant_scoped`.
- [ ] **Step 2: Run** `bash $S/ci.sh python -m pytest <file> -q -k runs`; expect FAIL.
- [ ] **Step 3: Implement** in SQL (parameters null-guarded like the existing `agent` guard). Run `EXPLAIN` on a representative tenant query if a database is available and note whether the existing indexes serve it; add migration 0503 only if the plan needs it (otherwise report none needed).
- [ ] **Step 4: Run** the runtime tests, `ruff`/`make lint` for the touched Python, regenerate the OpenAPI file and confirm only the new parameters changed.
- [ ] **Step 5: Commit** `feat(console): date window and status filters on the runs list`.

### Task 11: Assistant activity: last five assistants and the plain runs list

**Files:** Modify `WEB/components/console/RuntimeWorkspace.tsx` (rewrite the `RuntimeActivity` part; keep `AgentCard` exported; remove the dead `mode="agents"` branch and `AgentManagement`), create `WEB/lib/activity-view.ts`, `WEB/components/console/activity/LastAssistants.tsx`, `RunRow.tsx`; Modify `WEB/lib/identity-contracts.ts` only if run fields need types; Test `WEB/tests/activity-view.test.mjs`, `tests/test_activity_browser.py` (new), `tests/test_packaged_console.py` (update the activity part to open the arrow first), `WEB/scripts/check-isolated.mjs`.

**Interfaces (Produces, `lib/activity-view.ts`):**
- `lastAssistants(runs: AgentRun[], limit?: number): { avatarId: AgentId; title: string; finishedAt: string; outcome: "worked" | "needs_attention"; runId: number }[]`: newest first, de-duplicated by avatar id (`avatarForCapability` from `lib/agent-avatars.ts`), at most `limit` (default 5), unknown capabilities skipped, never padded.
- `outcomeOf(run: AgentRun): "worked" | "needs_attention" | "partial"`: `status === "ok"` = worked, `status === "error"` = needs attention, any other status (the contract allows `partial` though nothing produces it today) = `partial`, shown as a neutral "Partly finished" badge that no status chip selects (the server filter accepts only `ok` and `error`, so the labels must match it exactly; ruling from the Task 10 review). Query strings are built with `URLSearchParams`, `toISOString()` instants only, and `status` is omitted for "All" (an empty `status=` is a 422).
- `plainSummary(run: AgentRun, now: Date): string` ("Customer support agent finished 3 minutes ago") using `AGENT_TITLES`, relative time under an hour and a date after.
- `pageStack`: pure helpers `pushCursor(stack: number[], cursor: number): number[]`, `popCursor(stack): number[]` for Newer/Older.
- `agentOptions(): { value: string; label: string }[]` lists the nine capabilities; where two share a title the label adds the capability's short name ("Ticketing & handoff agent: escalation").

**UI:**
- `PageIntro` (the existing description text, unchanged), then a quiet line from the existing 24-hour overview ("Last 24 hours: N runs, M needed attention"; the numbers come only from the read and are omitted when it fails).
- `LastAssistants` strip (`data-testid="activity-last-assistants"`): up to five cards each with an `AgentAvatar` (no box), the assistant title, "finished <when>" and a badge "Worked" or "Needed attention" (text plus icon). On first render each avatar plays its wake animation once and then rests; a `needs_attention` avatar is shown still and dimmed. Motion is decoration (the badge and text carry the state); reduced motion or the pause preference shows the rest pose. Fewer than five cards when fewer agents have run; none shows "Nothing has run yet." The strip reads `/runs?limit=100` once on load and refreshes with the list poll on the newest page.
- Runs list: five rows per page (`limit=5`), Newer and Older buttons (`data-testid="activity-newer"`, `activity-older"`) using a stack of `before_id` cursors; each row is a `DetailsRow` whose summary is `plainSummary` plus the outcome badge; the opened block holds the originals verbatim: `#<id> <agent> / <status> / <error_code or Succeeded/Failed>`, the time, `<latency_ms or Unknown> ms`, trace id, and the Trace events list (with its existing `#<id> <node> / <agent or Coordinator> / <status> / <ms> ms` lines and the `Run #<id>` heading). Open rows are tracked by run id so the 5-second refresh never closes them.
- Filters row: `DateRangeFilter` (`activity-date`), an assistant `<select>` (`activity-agent`, `agentOptions`), `StatusChips` (`activity-status`: All, Worked, Needed attention). Any filter change resets to the newest page. Filters call the Task 10 parameters via `toInstants`. The existing free-text "Agent name (optional)" input and "Filter / refresh" button are replaced by these (their old strings are asserted only in the packaged test, update it).
- The list polls only on the newest page with no open filter change in flight; the run-detail poll (5 s while a row is open) stays.

- [ ] **Step 1: Write failing tests.** Node: `lastAssistants dedupes ticket and escalation under the tickets avatar`, `lastAssistants returns fewer than five and never pads`, `lastAssistants skips unknown capabilities`, `outcomeOf maps ok and error`, `plainSummary relative times`, `pageStack push and pop`, `agentOptions labels are unique`. Browser (stubbed upstream; the stub records query strings): `test_five_rows_per_page_with_newer_and_older`, `test_arrow_opens_details_with_original_text_and_survives_refresh`, `test_date_range_sends_half_open_instants_and_resets_to_newest`, `test_status_chip_sends_status`, `test_assistant_select_sends_agent`, `test_last_assistants_strip_dedupes_and_labels_outcomes`, `test_error_run_shows_needs_attention_badge_and_still_avatar`, `test_strip_empty_state`, `test_activity_at_five_widths_in_three_themes_without_overflow`, `test_reduced_motion_shows_rest_pose_in_strip`.
- [ ] **Step 2: Run** node then browser; expect FAIL.
- [ ] **Step 3: Implement** with kit pieces; keep `AgentCard` and its imports intact for Services; remove only the dead branch.
- [ ] **Step 4: Run** the new tests, `tests/test_services_browser.py` and `tests/test_services_redesign_browser.py` (AgentCard still renders), theme-literals, eslint, typecheck. Do not run the packaged test locally (CI only); reason through the updated locators in the report.
- [ ] **Step 5: Commit** `feat(console): plain Assistant activity with the last five assistants and calendar filters`.

### Task 12: Knowledge base: calmer layout and independent sections

**Files:** Modify `WEB/components/console/KnowledgeWorkspace.tsx` (read `facts/activity-and-knowledge-base.md` section 2 first), create `WEB/lib/knowledge-view.ts` and `WEB/components/console/knowledge/` pieces as needed; Test `WEB/tests/knowledge-view.test.mjs`, `tests/test_knowledge_browser.py` (new), `WEB/scripts/check-isolated.mjs`.

**Interfaces:** `lib/knowledge-view.ts`: `groupSources(files, documents, jobs): { library: Card[]; reading: Card[]; attention: Card[] }` where `Card = { id: string; title: string; kind: string; addedAt: string | null; chip: "ready" | "reading" | "needs_attention"; details: { label: string; value: string }[] }`; documents are In your library (`ready`), queued or running jobs are Being read (`reading`), failed jobs are Needs attention (`needs_attention`, the existing retry action kept); `pollingNeeded(groups): boolean` true only while any card is `reading`.
UI (all existing strings stay verbatim; new text is new elements): `PageIntro`; an "Add knowledge" area with the existing upload control and three plain steps beside it ("1 Add a file or write a note", "2 Your assistant reads it", "3 It answers from it"); the three groups as cards with a status chip and a `DetailsRow` holding size, job state, chunk counts, errors and the existing download and delete-source actions; the gaps list as a to-do style list with the existing actions; the embedding-compatibility panel, "Reconcile orphaned storage" and the "Writes require subscription and switch" notices move into a "Maintenance" disclosure (text unchanged); the agent on/off panel into a quiet "Settings" disclosure at the bottom. Each of the five lists loads independently: one failing call shows an inline retry for its section only and never blanks the page; the 5-second refresh runs only while `pollingNeeded`, then stops. Package-gate 403 `package_inactive` keeps today's sentence.

- [ ] **Step 1: Write failing tests.** Node: `groupSources puts documents, queued jobs and failed jobs in the right group`, `pollingNeeded only while reading`, `chips for unknown job states fall back to needs_attention`. Browser: `test_one_failing_list_does_not_blank_the_page`, `test_refresh_stops_when_nothing_is_reading`, `test_cards_open_details_with_original_actions`, `test_maintenance_disclosure_holds_reconcile_and_embedding_panel`, `test_package_inactive_sentence_unchanged`, `test_knowledge_at_five_widths_in_three_themes`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests, theme-literals, eslint, typecheck.
- [ ] **Step 5: Commit** `feat(console): calmer Knowledge base with independent sections`.

### Task 13: Customer inbox: friendlier layout and interaction

**Files:** Modify `WEB/components/console/inbox/Inbox.tsx`, `ConversationList.tsx`, `OpenConversation.tsx`, `Thread.tsx`, `ReviewCard.tsx` (layout and interaction only), `WEB/components/console/inbox/useInboxActions.ts` only for the confirm step; Test `tests/test_inbox_redesign_browser.py` (extend), `tests/test_whatsapp_browser.py` (must still pass untouched). Read `facts/inbox-email-business.md` section 1 first.

**Interfaces:** every existing string and `data-testid` stays (the inbox tests pin wording such as "Take over", "Queue reply", "Human reply", "Retry the same action"). The channel `<select>` (`inbox-channel-filter`) stays a select. "Needs review" and sender kind become single-select `StatusChips` (`testId="inbox-view"`, same values as today's `view` state). Search keeps its two-character rule and gains a clear button. Conversation rows: avatar (customer avatar), name, last-message preview (one line, truncated), relative time, and a small marker when it needs review; clearer empty and no-match states with one next action as NEW text; the composer area in the open conversation stays visible at the bottom (sticky) on narrow screens. **Queue reply gains a Confirm/Cancel step** like Approve and Reject (same `ProposalCard` pattern or an inline confirm); Enter cannot confirm by accident and Escape cancels; focus returns to the Queue reply button.

- [ ] **Step 1: Write failing tests** `test_queue_reply_asks_for_confirmation_and_cancel_sends_nothing`, `test_queue_reply_confirm_sends_once` (double click sends once), `test_inbox_view_chips_filter_like_the_old_control` (same request parameters as before), `test_search_clear_button_resets`, `test_rows_show_preview_time_and_review_marker`, `test_composer_sticky_at_390`, `test_inbox_no_overflow_at_five_widths_in_three_themes`.
- [ ] **Step 2: Run** the new tests; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_inbox_redesign_browser.py`, `tests/test_whatsapp_browser.py`, node inbox tests, eslint, typecheck.
- [ ] **Step 5: Commit** `feat(console): friendlier customer inbox with a confirm step for Queue reply`.

### Task 14: Email assistant: status bar, chips and confirmations

**Files:** Modify `WEB/components/console/EmailWorkspace.tsx`, `WEB/components/console/EmailPreview.tsx` (classes and tokens only; the dock shares it), create `WEB/lib/email-view.ts`; Test `WEB/tests/email-view.test.mjs`, `tests/test_email_management_browser.py` (must keep passing; it pins `POST email/sync` and `GET email/attention` call counts, so add no calls to those), `WEB/scripts/check-isolated.mjs`. Read `facts/inbox-email-business.md` section 2 first.

**Interfaces (`lib/email-view.ts`):** `filterThreads(threads, { unread: boolean; hasDraft: boolean; category: string | null; priority: string | null; range: DayRange }, draftThreadIds: Set<string>): Thread[]` (client-side over the loaded cache; date uses the thread's last activity in the browser's days); `cacheNote(count: number): string` returns "Showing the last 90 days, up to 200 emails." (shown whenever any filter is on, and as a quiet line otherwise).
UI: a status bar on top (mailbox connected, last sync time, the existing Sync action); `StatusChips` multi "Unread", "Has a draft"; category and priority `<select>`s; `DateRangeFilter`; the cache note; a tidier thread list and a reading pane with the reply box docked; the draft editor stays inline in the reading pane (NOT a drawer) so the Send dialog remains the only dialog; platform administrators see company names in the picker via ONE extra `GET /admin/clients` (platform admin only), falling back to the id. Existing wording and test ids stay. **Added confirmation:** Delete draft, Disconnect Gmail and Switch off for tenant each get a Confirm/Cancel step (Escape cancels, focus returns to the trigger). The Send dialog, version check, idempotency key and the stuck-send release flow are untouched.

- [ ] **Step 1: Write failing tests.** Node: `filterThreads unread`, `filterThreads has a draft`, `filterThreads date range uses local days`, `filterThreads combines filters`, `cacheNote text`. Browser: `test_chips_filter_loaded_threads_without_new_requests`, `test_cache_note_shown_with_filters`, `test_delete_draft_asks_first_and_cancel_deletes_nothing`, `test_disconnect_and_switch_off_ask_first`, `test_platform_admin_picker_shows_company_names`, `test_only_one_dialog_when_sending_from_a_draft`, `test_email_at_five_widths_in_three_themes` (this includes 320, where the page overflows today).
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_email_management_browser.py`, the new tests, node tests, eslint, typecheck.
- [ ] **Step 5: Commit** `feat(console): friendlier email assistant with chips, a status bar and confirmations`.

### Task 15: Backend: order filters and counts

**Files:** Modify `db/sql/business/list_orders.sql`, create `db/sql/business/order_counts.sql`, modify `csa/console/business.py` (query parameters, `OrderCounts` model, new route), `docs/CONSOLE_API.md`, regenerated `docs/console_openapi.json`; Test the existing business console test file extended (find it with grep `business/orders` under `tests/`); migration `db/migrations/0503_orders_created_index.sql` ONLY if the query-plan check needs it.

**Interfaces:** `GET /console/v1/business/orders` gains optional `from`, `to` (on `created_at`, `from` inclusive, `to` exclusive, ISO with offset), `status`, `payment_status` (validated against the existing enums; unknown value 422). Existing `channel`, `shop_id`, cursor and ordering `(created_at, id)` unchanged; no parameters means today's response. New `GET /console/v1/business/orders/counts?from&to&payment_status&channel&shop_id` returns `{ total: int, by_status: { "<status>": int } }` for the same date and payment filters but IGNORING any `status` parameter (so every chip shows its own number); statuses with zero rows appear with 0. Tenant scoped like its sibling. Orders with a null `placed_at` still list (the date filter is on `created_at`).

- [ ] **Step 1: Write failing contract tests** `test_orders_filter_by_created_window_half_open`, `test_orders_filter_by_status_and_payment_status`, `test_orders_filters_combine_with_cursor_paging`, `test_orders_without_filters_unchanged`, `test_orders_reject_unknown_status_and_inverted_range`, `test_order_counts_ignore_the_status_filter_and_include_zeros`, `test_order_counts_tenant_scoped`, `test_order_with_null_placed_at_is_listed`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement.** Run `EXPLAIN` on a representative tenant query if a database is available; if the sort and filter on `(client_id, created_at)` is not served by an existing index, add migration 0503 (`CREATE INDEX IF NOT EXISTS`, not concurrent, noted in `db/migrations/README.md` as 0500 to 0509 range) and keep `EXPECTED_SCHEMA_VERSION` at "0520"; otherwise add none and say so in the report.
- [ ] **Step 4: Run** the business tests, `make lint` for the touched Python, regenerate OpenAPI and confirm only the new parameters and route changed.
- [ ] **Step 5: Commit** `feat(console): order filters and per-status counts`.

### Task 16: Business management: cards, arrows and calendar filters

**Files:** Modify `WEB/components/console/BusinessWorkspace.tsx`, create `WEB/lib/business-view.ts`, `WEB/lib/business-api.ts` additions for the new parameters and counts; Test `WEB/tests/business-view.test.mjs`, `tests/test_business_browser.py` (new), `WEB/tests/identity-transport.test.mjs` (update the source assertions it makes on `BusinessWorkspace.tsx`), `WEB/scripts/check-isolated.mjs`. Read `facts/inbox-email-business.md` section 3 first.

**Interfaces (`lib/business-view.ts`):** `orderStatusChips(): { value: string; label: string }[]` with plain forms ("Waiting for confirmation" for `pending_confirmation`, "Being prepared" for `processing`, and so on; one entry per enum value; raw codes appear in the opened details), `paymentLabel(status)`, `formatRm(amount: number | string, currency: string): string` ("RM 1,234.50" for MYR; other currencies keep their code), `orderSummary(order): { title: string; subtitle: string }`.
UI: the Orders section as cards built only from fields the order read returns (reference, channel, total, status chip, payment chip, date); a `DetailsRow` per order shows the payment status, `placed_at`, `shipped_at`, `created_at`, `updated_at`, channel and shop ids, and the raw status and currency codes; products and local store records get the same card plus arrow treatment; filters: `DateRangeFilter` (`business-date`, created date), `StatusChips` (`business-status`, counts from the counts read, shown only when that read succeeds), a payment-status `<select>` (`business-payment`), plus the existing channel and shop filters; "Older" now ACCUMULATES rows instead of replacing them (fix `useRecords`). Counts: the page sends a default date window of the last 30 days when requesting counts (the unwindowed per-tenant scan is slow for big tenants; "All time" is an explicit choice in the date filter's presets area or a clear action and may be slow), fires the counts request in parallel with the list and aborts it on a filter change; every query string is built with `URLSearchParams` and `toISOString()` instants (a raw `+08:00` would decode to a space and give a 422). Must keep what `identity-transport.test.mjs` asserts: the DEMO MODE strings, the `function DemoBusinessWorkspace` / `function BusinessRecords` split, and exactly two disabled "Connect unavailable" / "Reconnect unavailable" buttons (update the test only where the redesign legitimately changes source text, and list each change).

- [ ] **Step 1: Write failing tests.** Node: `orderStatusChips covers every enum value`, `formatRm for MYR and another currency`, `paymentLabel`, `orderSummary`. Browser: `test_orders_show_cards_and_arrow_details`, `test_date_range_status_and_payment_filters_send_the_right_query`, `test_status_counts_show_and_hide_when_the_counts_read_fails`, `test_older_accumulates_rows`, `test_business_at_five_widths_in_three_themes`, `test_demo_mode_strings_and_disabled_connect_buttons_remain`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests, `WEB/tests/identity-transport.test.mjs`, theme-literals, eslint, typecheck.
- [ ] **Step 5: Commit** `feat(console): plain Business management with cards, arrows and calendar filters`.

### Task 17: Subscriptions

**Files:** Modify `WEB/components/console/SubscriptionsWorkspace.tsx` (read `facts/subscriptions-team-avatar-chat.md` section 1 first), create `WEB/lib/subscriptions-view.ts`; Test `WEB/tests/subscriptions-view.test.mjs`, `tests/test_subscriptions_browser.py` (extend, keep passing), `WEB/scripts/check-isolated.mjs`.

**Interfaces (`lib/subscriptions-view.ts`):** `summaryCounts(customers, requests, hasMoreCustomers: boolean, hasMoreRequests: boolean, now: Date): { active: string; endingSoon: string; requestsWaiting: string } | null` returns exact numbers as strings, or "50+" style (`${n}+`) where a further page exists, and `null` when any read failed (the row is then hidden); `endingSoon` = active customers whose period end is within 30 days or flagged cancel at period end; `formatInTenantZone(iso: string, timeZone: string | null): string`.
UI: the three tabs and the three-step change flow stay. Add: the summary row (real numbers only); visible filter labels and `StatusChips` on the customer list; a short new help line beside the jargon fields ("Cancel at period end", "Special price in RM", "Short code cannot change later") without changing those labels; friendlier empty states with one next action (new text); the timeline shows times in the tenant time zone (it uses the browser's today, `SubscriptionsWorkspace.tsx` about line 470); the drawer is sectioned (who, plan, price, when) and its review step shows a plain sentence summary first with the raw values inside a `DetailsRow`. No backend change.

- [ ] **Step 1: Write failing tests.** Node: `summaryCounts exact, plus-suffix and null`, `endingSoon window`, `formatInTenantZone Asia/Kuala_Lumpur vs UTC`. Browser: `test_summary_row_shows_and_hides_on_failure`, `test_customer_chips_filter`, `test_timeline_uses_the_tenant_time_zone`, `test_help_lines_beside_jargon_fields`, `test_review_step_shows_a_plain_sentence_and_raw_values_inside_the_arrow`, `test_subscriptions_at_five_widths_in_three_themes` (768 cuts a search placeholder today).
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests, `tests/test_subscriptions_browser.py`, theme-literals, eslint, typecheck.
- [ ] **Step 5: Commit** `feat(console): clearer subscriptions with a summary row and plain review step`.

### Task 18: Team

**Files:** Modify `WEB/components/console/TeamManagement.tsx` and `InvitationList.tsx` (layout and interaction; the invite logic, access defaults, mail flags and HR-access hooks are NOT touched), create `WEB/lib/team-view.ts`; Test `WEB/tests/team-view.test.mjs`, `tests/test_team_browser.py` (new), `tests/test_invite_access_browser.py` (must keep passing), `WEB/scripts/check-isolated.mjs`. Read `facts/subscriptions-team-avatar-chat.md` section 2 first. Do not touch `csa/console/identity.py` or `app/(auth)/invite/**`.

**Interfaces:** `lib/team-view.ts`: `initials(name: string, email: string): string`, `memberStatus(member): "active" | "disabled" | "invited"`. UI: members as cards (initials avatar, name, email, role pill, status chip) with a `DetailsRow` for last sign-in and access details; a primary "Invite someone" button opens the existing invite form in a `SlideOver` (the form and its test ids are unchanged); invitations as a compact list with status chips and a details arrow; the platform administrator's Members page (`/settings/team`) uses the same cards. **Added safety:** changing a role and "Disable access" each ask for a Confirm step whose NEW text says the person will be signed out (the server logs them out today and the UI never said so); "Revoke sessions" shows a confirmation message on success; Escape cancels; double click acts once; focus returns to the trigger.

- [ ] **Step 1: Write failing tests.** Node: `initials`, `memberStatus`. Browser: `test_member_cards_show_role_and_status`, `test_role_change_asks_first_and_cancel_changes_nothing`, `test_disable_access_confirm_mentions_sign_out_and_acts_once`, `test_revoke_sessions_shows_confirmation`, `test_invite_opens_in_a_drawer_and_the_form_still_works`, `test_platform_members_page_uses_the_same_cards`, `test_team_at_five_widths_in_three_themes`.
- [ ] **Step 2: Run**; expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests, `tests/test_invite_access_browser.py`, node tests, eslint, typecheck.
- [ ] **Step 5: Commit** `feat(console): friendlier team page with confirm steps`.

### Task 19: Verification, final preview and notes

**Files:** Modify whatever the checks find; scratchpad `$S/services_stub.py`, `$S/launch_preview.sh`, `$S/pr-notes-phase-2.md`.

- [ ] **Step 1:** Extend the preview stub so every changed screen shows synthetic data: runs with `from`, `to`, `status` and a mix of agents (at least seven distinct avatars, some errors), orders with filters and counts, knowledge-base groups including a reading and a failed job, email threads with unread and drafts, subscriptions with 60 customers (to show "50+"), team members and invitations, a processing chat turn.
- [ ] **Step 2:** Restart the preview on `http://127.0.0.1:3100/login` with the current code and leave it running; warm every page.
- [ ] **Step 3:** Screenshot and LOOK at every changed screen at 320, 390, 768, 1024 and 1440 in cobalt, night and bureau; fix overflow, clipping, contrast and focus problems in files this branch owns; list residuals in other owners' files.
- [ ] **Step 4:** Run (one at a time) node tests, eslint, typecheck, `make lint`, each changed browser file once, then `node scripts/check-isolated.mjs` in the background and report its outcome verbatim; confirm `next build` passes inside it.
- [ ] **Step 5:** Append to the PR notes: the phase 3 changes, the backend additions (runs filters, order filters and counts, 0503 or none), the confirm steps added, the held phase D, the label round still to do (Knowledge base name options "Help library", "What the assistant knows", "Business knowledge"), and the `pixelate_avatars --check` manual run for the new avatar. Commit `chore(console): phase 3 verification fixes`.
