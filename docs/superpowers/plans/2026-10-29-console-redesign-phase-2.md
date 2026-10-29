# Console Redesign Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the cobalt shell, three switchable themes, eleven animated pixel avatars, the redesigned Services and agent cards with server-confirmed animation, a "last changed" line, and the phase 1 follow-ups, in one branch.

**Architecture:** Themes are token sets selected by `data-theme` on `<html>` (server-read cookie, so no flash); owned files are converted to tokens and other owners' files are recoloured by remapping Tailwind's `--color-white` per theme. Pixel avatars are generated run-length-encoded grids (a checked-in Python script writes a checked-in TS data module) rendered by `AgentAvatar` as inline SVG whose frames switch with CSS steps gated by the existing motion rules. Services animation state comes from a small pure reducer that only advances on a confirmed server response.

**Tech Stack:** Next.js 16.3.8 (read `node_modules/next/dist/docs/` before touching cookies, headers or the root layout), React 19.2.8, Tailwind 4.3.3, `lucide-react`, node `--test` (`tests/*.test.mjs`), Python 3.12 FastAPI/psycopg, Playwright (python) stubbed-upstream browser tests, PostgreSQL migrations.

**Spec:** `docs/superpowers/specs/2026-10-29-console-redesign-phase-2-design.md` (read it first; it also names the phase 1 design and `.claude/prompts/_common-context.md` in the main checkout at `/home/amsyrzmi/CSAV2/.claude/prompts/`).

## Global Constraints

- Web root `CSA_WebConsole/csa-console/web` (WEB). Every repo test or lint command goes through the wrapper `/tmp/claude-1000/-home-amsyrzmi-CSAV2/44845bab-014a-4610-95de-184b6a6d4038/scratchpad/ci.sh` (exports `NO_LOCAL_ENV=1`, `ENV=ci`, `PATH=/tmp/fourstacks-toolbin:$PATH`); worktree sessions refuse `VAR=value` prefixes and compound commands. Python: `/home/amsyrzmi/CSAV2/.venv/bin/…`. Web `typecheck` needs `CSA_CONSOLE_API_ORIGIN` set to any dummy value (use the wrapper with `env`-free form: add it to a second wrapper if needed). Node is Playwright's bundled node at `/tmp/claude-1000/-home-amsyrzmi-CSAV2/44845bab-014a-4610-95de-184b6a6d4038/scratchpad/bin/node` (not on PATH).
- Local testing: only the tests for changed files, one test file per invocation; web `npm run lint`, `npm run typecheck`, `npm test`; `make lint`. The full suite is CI's job. Check `free -h` and `docker ps --filter label=org.testcontainers=true` before heavy runs.
- An editor hook reflows `.py` to 120 columns: write Python via script/heredoc, run `ruff format`, check `git diff` for unrelated reflow.
- Keep every existing `data-testid` stable (`tests/test_*_browser.py`, `tests/test_packaged_console.py` depend on them). A test that must change changes in the same commit with the reason in the message.
- New `lib/` or test files are added to the allowlist in `WEB/scripts/check-isolated.mjs` (`components/console/` is already covered).
- Do not edit: `TeamManagement.tsx`, `InvitationList.tsx`, `csa/console/identity.py`, invite parts of `csa/console/contracts.py`, `csa/hr/access_*`, `components/console/HrAccess*`, `lib/hr-access-*`, `Hr*.tsx`, `docs/PROGRESS.md`. Never enable FORCE RLS. No new console roles. No secrets; synthetic data only.
- Migrations only 0501 and 0502 (range 0501–0509). `EXPECTED_SCHEMA_VERSION` in `apps/gateway/main.py` must equal the highest migration in `db/migrations/`; tests import it, never hardcode it. Task 17's migration (0502) is gated on the owner's confirmation (see Task 17).
- `proxy.ts` matcher exclusion stays exactly `fourstacks/`; `public/fourstacks/` holds only public avatar images. The existing `public/fourstacks/agents/*.svg` files stay in place (nothing deleted).
- Theme names: `cobalt` (default), `night`, `bureau`; cookie `fs-theme` (SameSite=Lax, one year); rail cookie `fs-rail` (`collapsed` | `expanded`). Contrast: 4.5:1 text, 3:1 large text and control borders, in every theme.
- Avatar hues (degrees): support 225, knowledge 172, orders 28, tickets 50, ecommerce 145, invoices 200, assistant 268, hr 330, email 5, subscriptions 95, bookings 305; shared outline `#15203A` and white eyes. Grid sizes 32 and 48; allowed rendered sizes 32, 64 (32 grid) and 48, 96, 144 (48 grid). Idle loop: four frames, one second.
- Package to avatar: customer_support→support, orders_products→orders, subscriptions→subscriptions, bookings→bookings, marketplaces→ecommerce, hr→hr, staff_email→email, e_invoicing→none (icon plus the word "Planned"). Capability to avatar stays as in `lib/agent-avatars.ts`.
- Copy follows `docs/WRITING_GUIDE.md`; statuses are words, never colour or dots alone; no tracked-capital labels or eyebrows.
- Commit locally after every task, message ending with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Do not push, open PRs or merge.

## Review Focus

- A tampered or unknown `fs-theme` / `fs-rail` cookie value: falls back to `cobalt` / `collapsed`, never crashes or injects into the HTML (Task 5, Task 9).
- A user with reduced motion, pause on, or an offscreen row: sprites are the still first frame and every status is still readable as words (Task 11, Task 14).
- A service or agent action that fails after a delay, or is double-clicked: the avatar never animates, the row shows the server's reason, and only one request is sent (Task 14, Task 16).
- Phone search with country-code variants and short digit strings: `0125550148`, `60125550148`, `+60125550148` find the same contact, and a 3-digit query does not match every number (Task 2).
- A tenant member must never receive an actor name or id in `last_change`, and the 0502 function must not return another tenant's rows (Task 17).
- Rail collapsed: every link keeps its exact accessible name, and the page has no horizontal overflow at 320 px in all three themes (Task 8, Task 19).

---

## Group A: Phase 1 follow-ups

### Task 1: Inbox list indexes (migration 0501)

**Files:**
- Create: `db/migrations/0501_inbox_list_indexes.sql`
- Modify: `apps/gateway/main.py` (`EXPECTED_SCHEMA_VERSION` to `"0501"`), `db/migrations/README.md` (range 0501–0509 note)
- Test: `tests/test_migrate.py` (add), run `tests/test_health.py`

**Interfaces:**
- Produces: indexes `orders_client_contact_idx (client_id, contact_id)`, `subscriptions_client_contact_idx (client_id, contact_id)`, `approvals_client_conversation_idx (client_id, conversation_id)`. Leading `client_id` is included because every inbox query filters on it (my call; the owner approved indexes on the three contact/conversation columns). Check `pg_indexes` first and skip any that already exist in an equivalent form.

- [ ] **Step 1: Write the failing test** `test_inbox_list_indexes_exist` in `tests/test_migrate.py`: after migrations, `SELECT indexname FROM pg_indexes WHERE schemaname='public'` contains the three names above.
- [ ] **Step 2: Run** `ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_migrate.py -k inbox_list_indexes -q`; expect FAIL.
- [ ] **Step 3: Create the migration** with header comments in the style of `0500` (purpose, pre-check, safe to run twice via `CREATE INDEX IF NOT EXISTS`, rollback `DROP INDEX`), no `CONCURRENTLY` (the runner applies files in a transaction). Bump `EXPECTED_SCHEMA_VERSION`; update the README range line.
- [ ] **Step 4: Run** the new test, then `tests/test_health.py`, then `tests/test_migrate.py` in full, one file per invocation; expect PASS.
- [ ] **Step 5: Commit** `feat(db): indexes for the inbox list query (0501)`.

### Task 2: Phone search normalisation

**Files:**
- Modify: `db/sql/inbox/list_conversations.sql` (the phone OR branch), `csa/console/inbox_contact.py` (only if a new helper is needed)
- Test: `tests/test_console_inbox.py` (extend `test_search_matches_name_phone_formats_and_escapes`), `tests/test_inbox_contact.py` for any parser change

**Interfaces:** `parse_search(q) -> Search | None` keeps its signature (`like`, `digits`). The SQL must match when either side contains the other's national significant number: stored digits with leading zeros stripped (`regexp_replace(digits,'^0+','')`) must be a suffix of the query digits, when that stripped value has at least 7 digits; the existing "stored contains query" branch stays.

- [ ] **Step 1: Write failing tests** in `test_console_inbox.py`: a contact stored as `0125550148` is returned for `q` = `+60125550148`, `60125550148`, `0125550148`, `125550148`; a contact stored `+60125550148` is returned for `0125550148`; a 3-digit query (`012`) returns only numbers containing `012` (not every number); another tenant's contact never appears.
- [ ] **Step 2: Run** `ci.sh … pytest tests/test_console_inbox.py -k search -q`; expect the first assertion to FAIL.
- [ ] **Step 3: Implement** the SQL change described above; keep the query's named-parameter style and `%%` escaping.
- [ ] **Step 4: Run** `tests/test_console_inbox.py` and `tests/test_inbox_contact.py`; expect PASS. Run `make lint`.
- [ ] **Step 5: Commit** `fix(inbox): search finds a phone stored in local format by its international form`.

### Task 3: Phone layout focus and `scale` transition

**Files:**
- Modify: `WEB/components/console/inbox/Inbox.tsx` (Back handler), `WEB/components/console/ui.tsx` (`motion` string gains `scale`)
- Test: `tests/test_inbox_redesign_browser.py` (new test)

**Interfaces:** After Back on the phone layout, keyboard focus lands on the list row of the conversation that was open (fall back to the list region heading when that row is gone). The `motion` transition list in `ui.tsx` includes `scale` so `active:scale-[0.98]` animates.

- [ ] **Step 1: Write the failing browser test** `test_back_on_phone_returns_focus_to_the_open_conversation_row` (390 px viewport, stubbed upstream as in the file's existing tests): open a row, press Back, assert that row is focused.
- [ ] **Step 2: Run** `ci.sh … pytest tests/test_inbox_redesign_browser.py -k back_on_phone -q`; expect FAIL.
- [ ] **Step 3: Implement** focus restore in `Inbox.tsx`; add `scale` to `motion` in `ui.tsx`.
- [ ] **Step 4: Run** the new test then the whole file; `npm run lint`, `npm run typecheck`.
- [ ] **Step 5: Commit** `fix(inbox): focus returns to the conversation row after Back on phone`.

### Task 4: Harden two assertions, add the "Approval expired." test

**Files:** Test only: `tests/test_inbox_redesign_browser.py`

- [ ] **Step 1:** In `test_slide_over_opens_closes_with_escape_and_returns_focus` assert that while the dialog is open focus is inside the dialog and `expect(details).not_to_be_focused()`; keep the existing return-focus assertion after close.
- [ ] **Step 2:** In `test_unnamed_whatsapp_sender_shows_masked_number_in_list_and_full_in_card` read `outerHTML` of the list and assert it contains the masked number and not the full number, and assert `inbox-thread` text lacks the full number.
- [ ] **Step 3:** Add `test_expired_approval_shows_approval_expired` using the stubbed upstream returning the server's expired-approval conflict on decide; assert the page shows `Approval expired.` and the review card is not removed as handled. Find the exact status and body in `csa/console/inbox.py::decide_approval` and the UI string in `WEB/components/console/inbox/` (`grep "Approval expired"`).
- [ ] **Step 4: Run** the three tests, then the file; each new assertion must fail if the behaviour is removed (temporarily break it to confirm, then restore).
- [ ] **Step 5: Commit** `test(inbox): harden two weak assertions and cover Approval expired`.

---

## Group B: Themes and shell

### Task 5: Theme tokens, cookie helpers and no-flash root

**Files:**
- Create: `WEB/app/themes.css`, `WEB/lib/theme.ts`, `WEB/tests/theme.test.mjs`, `WEB/tests/theme-contrast.test.mjs`
- Modify: `WEB/app/layout.tsx`, `WEB/app/globals.css` (import `themes.css`; map bridge tokens `--color-ink-*`, `--color-brand-*`, `--color-white` onto per-theme values), `WEB/scripts/check-isolated.mjs`

**Interfaces:**
- Produces in `lib/theme.ts`: `export const THEMES = ["cobalt","night","bureau"] as const; export type Theme = (typeof THEMES)[number]; export const THEME_COOKIE = "fs-theme"; export function parseTheme(raw: string | null | undefined): Theme` (unknown/empty to `"cobalt"`); `export function themeCookie(theme: Theme): string` (full `Set-Cookie`-style string: `fs-theme=<t>; Path=/; Max-Age=31536000; SameSite=Lax`); `export function writeTheme(theme: Theme): void` (client: sets `document.cookie`, mirrors `localStorage["fs-theme"]`, sets `document.documentElement.dataset.theme`).
- `app/themes.css` defines, per `[data-theme="cobalt"|"night"|"bureau"]` (cobalt is also `:root`), every `--fs-*` colour token already in `app/fourstacks-tokens.css`, plus `--color-white` and `--fs-on-primary` (text colour on the accent), `--fs-shell`, `--fs-rail`, `--fs-tile`, and `color-scheme`. Values: cobalt accent `#0047ab`; night accent about `#6ea2ff` on dark navy; bureau paper `#f6f1e7`, hard 3px offset shadow, 0 radius on controls. Starting values are in the spec section 5 and the brainstorm mockup constants (`gen_mock3.py`, `C` dict, in the session scratchpad); the contrast test is the authority.
- `app/layout.tsx` reads the cookie with `cookies()` (check the Next 16 docs: it is async) and sets `data-theme` on `<html>`.

- [ ] **Step 1: Write failing tests.** `theme.test.mjs`: `parseTheme` returns each valid name, `"cobalt"` for `null`, `""`, `"NIGHT"`, `"night; Path=/"`, `"<script>"`; `themeCookie("night")` contains `fs-theme=night`, `SameSite=Lax`, `Max-Age=31536000`. `theme-contrast.test.mjs`: parse `app/themes.css`, and for each theme assert WCAG ratio ≥4.5 for ink/surface, muted/surface, ink/shell, on-primary/primary, primary/surface as text link (≥4.5), border-control/surface (≥3), and for the Services status words in `--fs-success`, `--fs-warning`, `--fs-danger` on their `-bg`.
- [ ] **Step 2: Run** `ci.sh … node --test tests/theme.test.mjs` (and the contrast file); expect FAIL (files missing).
- [ ] **Step 3: Implement** `lib/theme.ts`, `themes.css`, the layout change and the `globals.css` bridge. Add new files to `check-isolated.mjs`.
- [ ] **Step 4: Run** both node tests, `npm run lint`, `npm run typecheck` (dummy `CSA_CONSOLE_API_ORIGIN`), `npm test`.
- [ ] **Step 5: Commit** `feat(console): theme tokens for cobalt, night and bureau with a server-read cookie`.

### Task 6: Token sweep, shared files

**Files:**
- Modify: `WEB/components/console/ui.tsx`, `WEB/components/console/fs/StatusLabel.tsx`, `fs/SlideOver.tsx`, `fs/Skeleton.tsx`, `fs/ListStates.tsx`, `fs/CustomerAvatar.tsx`, `WEB/app/globals.css` (hard-coded colours in component classes)
- Create: `WEB/tests/theme-literals.test.mjs`
- Do not touch `fs/AgentAvatar.tsx` (replaced in Task 11).

**Interfaces:** `theme-literals.test.mjs` exports nothing; it reads a list `OWNED` of files (ui.tsx, fs/*, inbox/*, AppShell, shell/*, ServicesWorkspace, RuntimeWorkspace, globals.css) and fails for any `#rrggbb` literal, `bg-white`, `text-white`, `border-white` in them, except inside `app/themes.css`. Files not yet converted are listed in a `PENDING` array in the test that later tasks empty (inbox files in Task 7, the rest in their own tasks).

- [ ] **Step 1: Write the failing test** with `OWNED` containing the files of this task; expect it to list every literal found.
- [ ] **Step 2: Run** `ci.sh … node --test tests/theme-literals.test.mjs`; expect FAIL listing file:line.
- [ ] **Step 3: Convert** each literal to a token (`var(--fs-…)` or the bridge classes). `field` and `secondary` use `bg-[var(--fs-surface)]`.
- [ ] **Step 4: Run** the test, `npm test`, lint, typecheck. Open Overview and Services in the preview stack (scratchpad `launch_preview.sh`, stub gateway) in all three themes and confirm inputs and buttons are legible.
- [ ] **Step 5: Commit** `refactor(console): shared components use theme tokens only`.

### Task 7: Token sweep, inbox

**Files:** Modify `WEB/components/console/inbox/{Thread,MessageItem,ConversationList,ReviewCard,CustomerCard,Inbox}.tsx`; extend `OWNED` in `tests/theme-literals.test.mjs`.

- [ ] **Step 1: Add** the inbox files to `OWNED`; run; expect FAIL listing the literals (`#D8E1F0`, `#15203A`, `#183DA5` and others).
- [ ] **Step 2: Convert** to tokens, adding any missing semantic tokens (for example `--fs-bubble-customer`, `--fs-bubble-team`) to `themes.css` for all three themes with passing contrast (extend `theme-contrast.test.mjs`).
- [ ] **Step 3: Run** the literal test, contrast test, `npm test`, lint, typecheck; run `tests/test_inbox_redesign_browser.py` (one file) to confirm no testid regressions.
- [ ] **Step 4: Commit** `refactor(inbox): inbox components use theme tokens only`.

### Task 8: Shell: rail, navigation and mobile menu

**Files:**
- Create: `WEB/lib/shell-state.ts`, `WEB/components/console/shell/Rail.tsx`, `WEB/components/console/shell/MobileMenu.tsx`, `WEB/tests/shell-state.test.mjs`
- Modify: `WEB/components/AppShell.tsx`, `WEB/lib/navigation.ts` (add `icon` key names only if needed; keep `navigationFor` signature), `WEB/app/(admin)/layout.tsx` and the portal layout (pass the rail cookie value), `WEB/scripts/check-isolated.mjs`
- Test: `tests/test_console_shell_browser.py` (create; stubbed-upstream harness copied from `tests/test_services_browser.py`)

**Interfaces:**
- `lib/shell-state.ts`: `export type RailState = "collapsed" | "expanded"; export const RAIL_COOKIE = "fs-rail"; export function parseRail(raw: string | null | undefined): RailState` (default `"collapsed"`); `export const NAV_ICONS: Record<NavKey, string>` mapping every `NavKey` to a distinct lucide icon name (no two keys share an icon; today overview/chat/inbox collide).
- `AppShell` props unchanged (`me`, `packages`, `children`) plus an optional `initialRail?: RailState`.
- Rail: 56 px collapsed, 240 px expanded, brand mark links to the home (`/` or `/portal`), `<nav aria-label="Main navigation">`, each link has its exact existing label as visually hidden text when collapsed and a tooltip on hover and focus, `aria-current="page"` on the current page, an expand/collapse button (`data-testid="shell-rail-toggle"`) that writes the `fs-rail` cookie. Under 1024 px: a menu button (`data-testid="shell-menu"`) opens the full labelled list in a dialog.

- [ ] **Step 1: Write failing tests.** `shell-state.test.mjs`: `parseRail` defaults and tamper cases; `NAV_ICONS` has an entry for every key of `navigationFor` output and all values are unique. Browser `test_rail_collapsed_links_keep_exact_names` (`get_by_role("link", name="Services", exact=True)` found while collapsed), `test_rail_toggle_persists_across_reload`, `test_mobile_menu_lists_all_links_and_closes_with_escape`, `test_no_page_overflow_at_320_and_1440_in_each_theme`.
- [ ] **Step 2: Run** each; expect FAIL.
- [ ] **Step 3: Implement.** Keep `PageContextProvider` and `AssistantDock` mounted exactly as now; the main content sits in a rounded card on the `--fs-shell` ground (layout from the Services mockup).
- [ ] **Step 4: Run** node tests, the new browser file, `tests/test_services_browser.py` and `tests/test_subscriptions_browser.py` (fix link-name or visible-text assertions that the shell changes, listing each in the commit message), lint, typecheck. Update `theme-literals` `OWNED`/`PENDING`.
- [ ] **Step 5: Commit** `feat(console): cobalt shell with icon rail, labelled expand and mobile menu`.

### Task 9: Shell: top bar, user menu, theme control, Calm motion

**Files:**
- Create: `WEB/components/console/shell/TopBar.tsx`, `WEB/components/console/shell/UserMenu.tsx`, `WEB/components/console/shell/ThemeControl.tsx`
- Modify: `WEB/components/AppShell.tsx`
- Test: `tests/test_console_shell_browser.py` (extend)

**Interfaces:** Top bar shows the page title (derived from the active nav label), the theme control (`data-testid="shell-theme"`, a menu of the three themes using `writeTheme`), and the user menu (`data-testid="shell-user-menu"`) containing name, role label (`roleLabels`), a "Calm motion" switch calling `setMotionPaused` (`data-testid="shell-motion-toggle"`, `aria-checked` reflects the stored preference) and a button named exactly `Sign out` running the existing `identity.logout()` flow. The eyebrow "CSA Console" header is removed.

- [ ] **Step 1: Write failing browser tests:** `test_theme_switch_applies_and_persists_without_flash` (choose Night, reload, `html[data-theme="night"]` is present in the first server response HTML: assert via `page.request.get` body contains `data-theme="night"` with the cookie set), `test_tampered_theme_cookie_falls_back_to_cobalt`, `test_calm_motion_toggle_sets_pause_key` (localStorage `fs-motion-paused` is `"1"`), `test_sign_out_button_keeps_its_name_and_signs_out`.
- [ ] **Step 2: Run** them; expect FAIL.
- [ ] **Step 3: Implement** the three components; keyboard: menus open with Enter/Space, close with Escape, focus returns to the trigger.
- [ ] **Step 4: Run** the browser file and the two services/subscriptions browser files; lint; typecheck; `npm test`.
- [ ] **Step 5: Commit** `feat(console): top bar with theme control, Calm motion and user menu`.

---

## Group C: Pixel avatars

### Task 10: Conversion script and generated idle data

**Files:**
- Create: `scripts/pixelate_avatars.py`, `WEB/lib/pixel-avatars-data.ts` (generated, checked in), `WEB/tests/pixel-avatars-data.test.mjs`
- Reference (copy ideas, not files): the brainstorm scripts in the session scratchpad: `pixelate2.py` (16x supersample, mode-of-palette per cell, orphan removal), `gen_mock2.py` (`recolour`, per-agent `frames`, `MOTION`, `HUE`).

**Interfaces:**
- `scripts/pixelate_avatars.py` has `python -m scripts.pixelate_avatars [--check]`; it rasterises each `WEB/public/fourstacks/agents/<id>.svg` with Playwright, builds 32 and 48 grids, applies the hue map in Global Constraints, builds the four idle frames per grid with the per-agent motions in the spec section 6, and writes `lib/pixel-avatars-data.ts`; `--check` exits non-zero if the committed file differs from a fresh generation.
- `lib/pixel-avatars-data.ts` exports `PIXEL_DATA: Record<AgentId, { palette: string[]; idle: Record<"32"|"48", string[]> }>` where each frame string is run-length encoded rows (format documented in the file header and decoded in Task 11; a palette index of `-1` is transparent).

- [ ] **Step 1: Write the failing test** `pixel-avatars-data.test.mjs`: every `AGENT_IDS` entry present; each frame decodes to exactly 32×32 or 48×48 cells; four idle frames per grid; palette ≤ 12 entries, contains `#15203A` and `#FFFFFF`; palettes of the eleven agents are pairwise different; the data module's source size is under 150 KB.
- [ ] **Step 2: Run** `ci.sh … node --test tests/pixel-avatars-data.test.mjs`; expect FAIL (no module).
- [ ] **Step 3: Implement** the script, run it with the main venv (`python -m scripts.pixelate_avatars`), add test and data to the allowlist.
- [ ] **Step 4: Run** the test, `make lint`, `npm run typecheck`.
- [ ] **Step 5: Commit** `feat(console): pixel avatar generator and idle frame data`.

### Task 11: `AgentAvatar` renders pixel sprites with state and motion rules

**Files:**
- Create: `WEB/lib/pixel-avatars.ts` (decoder), `WEB/tests/pixel-avatars.test.mjs`
- Modify: `WEB/components/console/fs/AgentAvatar.tsx`, `WEB/app/globals.css` (frame animation keyframes), every caller using size 24 (`grep -rn "size={24}"` and `AvatarSize`), `WEB/tests/agent-avatars.test.mjs`, `tests/test_inbox_redesign_browser.py` (the avatar test at "agent_avatar_loads…" asserts an `img[src*='/fourstacks/agents/support.svg']`: change it to assert the inline sprite `svg[role='img'][aria-label=...]` loads and that the proxy still denies by default)

**Interfaces:**
- `lib/pixel-avatars.ts`: `export type SpriteGrid = 32 | 48; export function decodeFrame(encoded: string, grid: SpriteGrid): number[][]; export function frameRects(frame: number[][], palette: string[]): { x: number; y: number; w: number; fill: string }[]`.
- `AgentAvatar` props: `agentId: AgentId; size?: 32 | 48 | 64 | 96 | 144 (default 48); label?: string; state?: "idle" | "still" | "pending" | "working" | "wake"` (default `"idle"`). Sizes 32 and 64 use the 32 grid (1x, 2x), 48, 96 and 144 the 48 grid. Inline SVG with `shape-rendering="crispEdges"`, `role="img"`, `aria-label` = label (and `aria-hidden` when label is empty, as today). Server render and any state when `useMotionAllowed(ref)` is false show frame 0, still. `still`/`pending` are dimmed (`filter: saturate(.45) opacity(.75)`); `idle` and `working`/`wake` animate only when motion is allowed. Frames switch with CSS `steps(1)` opacity animation (the brainstorm's `show` keyframes), duration one second for idle. Keep the tile (rounded square, `--fs-tile` background) and the `data-agent` attribute for tests.

- [ ] **Step 1: Write failing tests.** `pixel-avatars.test.mjs`: `decodeFrame` round-trips a known RLE string; `frameRects` merges runs and skips transparent. Update `agent-avatars.test.mjs` to the new size union. Browser: `test_agent_avatar_renders_inline_and_is_still_under_reduced_motion` (emulate reduced motion; computed `animation-name` of frames is `none`).
- [ ] **Step 2: Run** each; expect FAIL.
- [ ] **Step 3: Implement.** Move 24 px callers to 32.
- [ ] **Step 4: Run** node tests, the inbox browser file, lint, typecheck.
- [ ] **Step 5: Commit** `feat(console): AgentAvatar renders generated pixel sprites with motion gating`.

### Task 12: Working loops and props

**Files:** Modify `scripts/pixelate_avatars.py`, regenerate `WEB/lib/pixel-avatars-data.ts`; extend `WEB/tests/pixel-avatars-data.test.mjs`; modify `AgentAvatar.tsx` (`working` and `wake` use the working frames).

**Interfaces:** `PIXEL_DATA[id].working: Record<"48", string[]>` with six to eight frames, each adding one small pixel prop beside the creature (support: speech card; knowledge: open book; orders: order card; tickets: ticket stack; ecommerce: parcel; invoices: invoice sheet with tick; assistant: proposal card; hr: name cards; email: two trays; subscriptions: renewal dial; bookings: calendar with ticking day). Props use only the agent's palette plus the outline and white. `working` loops continuously (when motion allowed); `wake` plays the working frames once for about 1.5 seconds, then settles to idle.

- [ ] **Step 1: Extend the data test:** every agent has 6–8 working frames of 48×48 whose pixels differ between frames, and a prop region (pixels outside the idle silhouette) exists.
- [ ] **Step 2: Run;** expect FAIL.
- [ ] **Step 3: Implement** in the generator (props as small hand-authored pixel stamps in the script), regenerate, and update `AgentAvatar` for `working` and `wake`.
- [ ] **Step 4: Run** node tests, `python -m scripts.pixelate_avatars --check`, lint, typecheck.
- [ ] **Step 5: Commit** `feat(console): pixel working loops with a prop per agent`.
- [ ] **Controller gate (not a subagent step):** after this commit the controller renders a contact sheet (all eleven: idle and working, three themes' tiles, 32 and 48 at real sizes) with the visual companion and waits for the owner's review. Hand-tidy requests change only the generator's override data and regenerate; they never block Group D.

---

## Group D: Services and agents

### Task 13: Services view helpers and package avatar mapping

**Files:**
- Create: `WEB/lib/services-view.ts`, `WEB/tests/services-view.test.mjs`
- Modify: `WEB/lib/agent-avatars.ts` (add `avatarForPackage`), `WEB/scripts/check-isolated.mjs`

**Interfaces:**
- `avatarForPackage(key: string): AgentId | null` per the mapping in Global Constraints.
- `lib/services-view.ts`: `export interface ServiceStats { active: number; agentsOn: number; waiting: number; suspended: number }`; `export function serviceStats(services: Service[]): ServiceStats` (`agentsOn` counts agents with `capability?.active`); `export function rowAvatarState(status: PackageStatus): "idle" | "still"` (active→idle, everything else→still); `export function statusLabel(service: Service): { text: string; tone: "ok" | "wait" | "off" | "neutral" }` reproducing today's texts (`Available`, `Requested`, `Active`, `Suspended`, `Coming soon` for unsellable available).

- [ ] **Step 1: Write failing tests** with the synthetic fixture the preview stub uses (8 services): `serviceStats` returns active 3, agentsOn 5, waiting 1, suspended 1; `avatarForPackage("e_invoicing")` is `null`; all seven others map; `statusLabel` for an unsellable available service is `Coming soon`.
- [ ] **Step 2: Run** `ci.sh … node --test tests/services-view.test.mjs`; expect FAIL.
- [ ] **Step 3: Implement.** **Step 4: Run** it plus `agent-avatars.test.mjs`, lint, typecheck. **Step 5: Commit** `feat(console): services view helpers and package avatar mapping`.

### Task 14: Confirmed-only avatar transition

**Files:** Create `WEB/lib/avatar-transition.ts`, `WEB/components/console/fs/useAvatarTransition.ts`, `WEB/tests/avatar-transition.test.mjs`; modify `check-isolated.mjs`.

**Interfaces:**
- `lib/avatar-transition.ts`: `export type Phase = "idle" | "still" | "pending" | "wake" | "settling"; export type Event = { type: "start" } | { type: "confirmed"; to: "active" | "inactive" } | { type: "failed" } | { type: "done" }; export function nextPhase(current: Phase, event: Event, motionAllowed: boolean): Phase`. Rules: `start` → `pending` from any phase; `confirmed` to `active` → `wake` (or `idle` when motion is not allowed); `confirmed` to `inactive` → `settling` (or `still`); `failed` → the phase before `start` (the hook remembers it); `done` ends `wake`→`idle` and `settling`→`still`. No event other than `confirmed` ever enters `wake`.
- `useAvatarTransition(initial: Phase): { phase: Phase; start(): void; confirm(to): void; fail(): void }` using `useMotionAllowed`; `wake` ends itself after 1500 ms.

- [ ] **Step 1: Write failing tests:** a table over all (phase, event) pairs; `start` then `failed` returns to the original phase; `wake` is unreachable without `confirmed`; with `motionAllowed=false` confirmed activation lands in `idle` not `wake`.
- [ ] **Step 2: Run;** expect FAIL. **Step 3: Implement.** **Step 4: Run** test, lint, typecheck. **Step 5: Commit** `feat(console): avatar transition reducer that only advances on server confirmation`.

### Task 15: `AgentCard` redesign (shared)

**Files:** Modify `WEB/components/console/RuntimeWorkspace.tsx` (`AgentCard` and its call site), extend `tests/test_services_browser.py` or create `tests/test_agents_card_browser.py`.

**Interfaces:** `AgentCard` keeps its props `{ agent: Agent; busy: boolean; onToggle: (agent, switchName) => void }` and adds optional `pending?: "is_enabled" | "client_enabled" | null` and `failed?: string | null`. It renders the agent's avatar (`avatarForAgent(agent.name)`, or an icon plus "Planned" for no avatar) with `useAvatarTransition`: avatar `idle` when `capability.active`, otherwise `still`; while `pending` it shows a pending label and a dimmed still avatar; the avatar only changes after the parent reports the new capability state. Buttons keep their exact current labels and are disabled while `busy`. Uses theme tokens only (remove it from `PENDING`).

- [ ] **Step 1: Write failing browser tests** on the Services page with the stubbed upstream: `test_agent_switch_shows_pending_then_confirms` (delayed 200 patch: during the delay the row shows the pending label and the avatar has no animation; after, the state text changes), `test_agent_switch_failure_keeps_old_state_and_shows_reason` (500/409 with `detail`), `test_double_click_sends_one_request`.
- [ ] **Step 2: Run;** expect FAIL. **Step 3: Implement** (the unlinked `agents` pages use `AgentCard` too; they must still render: add a render check in the browser test by visiting `/agents` with the stub). **Step 4: Run** the browser file(s), lint, typecheck. **Step 5: Commit** `feat(console): AgentCard with avatar, pending and failed states`.

### Task 16: `ServicesWorkspace` redesign

**Files:** Modify `WEB/components/console/ServicesWorkspace.tsx`; create `tests/test_services_redesign_browser.py`; update `tests/test_services_browser.py` only where visible structure changed.

**Interfaces:** Same export `ServicesWorkspace({ me, initialClientId })` and every existing `data-testid` (`services-card-<key>`, `services-status-<key>`, `services-activation-<key>`, `services-request-<key>`, `services-activate-<key>`, `services-suspend-<key>`, `services-agents-toggle-<key>`, `services-agents-<key>`, `services-activate-reference`, `services-activate-note`, `services-activate-confirm`, `services-suspend-note`, `services-suspend-confirm`, `services-tenant`, `services-group-<kind>`). New testids: `services-stat-active`, `services-stat-agents`, `services-stat-waiting`, `services-stat-suspended`. Layout per the mockup: four stat cards from `serviceStats`, a list card per group with dashed dividers and rows (avatar chip via `avatarForPackage`, title, summary, agent count, status word via `statusLabel`, action buttons); the confirm forms and the WhatsApp panel stay. `services-card-<key>` is now the row container. Each action uses `useAvatarTransition`: `start` on click, `confirm` after the API call resolves and the list reloads, `fail` on error (with the server's message in the existing error `Notice`). E-invoicing shows an icon and "Planned". Remove the file from `PENDING`.

- [ ] **Step 1: Write failing browser tests** (stubbed upstream with delay and failure switches): `test_activate_pending_then_wake_after_confirmation`, `test_suspend_settles_only_after_confirmation`, `test_failed_activation_keeps_row_and_shows_reason`, `test_stat_cards_show_real_counts`, `test_einvoicing_row_shows_planned_without_avatar`, `test_reduced_motion_rows_are_still`, `test_layout_fits_320_and_1440_in_every_theme`.
- [ ] **Step 2: Run;** expect FAIL. **Step 3: Implement.** **Step 4: Run** the new file, `tests/test_services_browser.py`, `tests/test_subscriptions_browser.py`, lint, typecheck, `npm test`. **Step 5: Commit** `feat(console): Services page in the cobalt design with confirmed-only avatar animation`.

### Task 17: "Last changed" backend (migration 0502; owner confirmation required before starting)

The owner chose option B (audit-based), but `csa_app` has only INSERT on `console_audit` and no access to `console_users` (migration 0270), so B needs a new migration. The controller confirms 0502 with the owner before dispatching this task; if declined, Tasks 17 and 18 are replaced by the option A line (existing row fields only).

**Files:**
- Create: `db/migrations/0502_console_last_change.sql`
- Modify: `apps/gateway/main.py` (`"0502"`), `db/migrations/README.md`, `csa/console/services.py`, `csa/console/agents.py` or `csa/console/capabilities.py` (wherever `CapabilityOut` lives), a new SQL file `db/sql/console/last_change.sql`, `docs/console_openapi.json` (regenerate with `python -m scripts.export_console_openapi`), `docs/CONSOLE_API.md`
- Test: `tests/test_console_services.py`, `tests/test_console_capabilities.py`, `tests/test_migrate.py`, `tests/test_health.py`, the contract snapshot test (`grep -rn console_openapi tests`)

**Interfaces:**
- SQL: `public.console_last_change(p_client uuid, p_target text, p_actions text[]) RETURNS TABLE(action text, at timestamptz, actor_id uuid, actor_name text)` `LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public`, returning the single newest `console_audit` row where `client_id = p_client`, `target_id = p_target` and `action = ANY(p_actions)`, joined to `console_users.display_name`; `REVOKE ALL … FROM PUBLIC; GRANT EXECUTE … TO csa_app`. Explicit `client_id` filter is mandatory (the function bypasses RLS as owner). No FORCE RLS. Rollback in the header: `DROP FUNCTION`.
- Python: `class LastChange(BaseModel): action: Literal["requested","activated","suspended","subscribed","unsubscribed","tenant_switch_on","tenant_switch_off"]; at: datetime; actor: str` where `actor` is the display name only when `actor.platform_admin`, else the literal `"a platform administrator"`; never the user id. `ServiceOut.last_change: LastChange | None` and `CapabilityOut.last_change: LastChange | None`, both `None` when no audit row exists. Actions come from `service.requested|activated|suspended` and `capability.updated` (derive subscribed/unsubscribed/switch from the `details` before/after: if details are not in the SQL result, return them as a `details jsonb` column and derive in Python).

- [ ] **Step 1: Write failing tests:** after activating a service as platform admin, `list_services` for that admin returns `last_change.action == "activated"` with the admin's display name; for a `tenant_admin` the same row returns `actor == "a platform administrator"` and no user id anywhere in the JSON; a second tenant's audit rows never appear; a never-changed service has `last_change is None`; a capability toggle yields the matching action; the OpenAPI snapshot test passes after regeneration.
- [ ] **Step 2: Run** each file, one per invocation; expect FAIL.
- [ ] **Step 3: Implement** migration, SQL, models, `list_services` and `list_agents` wiring (one query per service key; batch with `p_target` list only if profiling shows a problem). Regenerate the OpenAPI doc; add the field to `docs/CONSOLE_API.md`.
- [ ] **Step 4: Run** `tests/test_migrate.py`, `tests/test_health.py`, `tests/test_console_services.py`, `tests/test_console_capabilities.py`, the snapshot test; `make lint`.
- [ ] **Step 5: Commit** `feat(console): last_change on services and agents from the audit trail (0502)`.

### Task 18: "Last changed" line in the UI

**Files:** Modify `WEB/lib/services-api.ts`, `WEB/lib/identity-contracts.ts` (types `LastChange`), `WEB/lib/services-view.ts` (`lastChangeText(change: LastChange | null): string | null`), `ServicesWorkspace.tsx`, `AgentCard`; test `WEB/tests/services-view.test.mjs` and `tests/test_services_redesign_browser.py`.

**Interfaces:** `lastChangeText` returns e.g. `Activated 20 Oct by Preview Admin` / `Suspended 27 Oct by a platform administrator`, using the existing `toLocaleDateString` style; the activation line that exists today keeps its testid `services-activation-<key>`; the new line has `data-testid="services-last-change-<key>"` (and `agent-last-change-<name>` on agent cards). The line is hidden when `last_change` is null.

- [ ] **Step 1: Write failing tests** (unit for `lastChangeText` all actions and null; browser: platform admin sees a name, tenant member sees "a platform administrator").
- [ ] **Step 2: Run;** expect FAIL. **Step 3: Implement.** **Step 4: Run** tests, lint, typecheck. **Step 5: Commit** `feat(console): show when and by whom a service or agent was last changed`.

### Task 19: Responsive and theme verification, and cleanup

**Files:** Modify whatever the checks find; update `tests/theme-literals.test.mjs` so `PENDING` is empty; no docs/PROGRESS.md edits.

- [ ] **Step 1:** Update the scratchpad stub gateway (`services_stub.py`) to include `last_change` and run the preview stack (`launch_preview.sh`); take Playwright screenshots of Overview, Services (expanded agents), Inbox, Settings pages at 320, 390, 768, 1024, 1440 in each theme; fix overflow and contrast problems found.
- [ ] **Step 2:** Run Hallmark's linter if Node and Chrome allow: `node .claude/skills/hallmark/scripts/sloplint.mjs <built page> --render --scope app` (skill at `/home/amsyrzmi/CSAV2/.claude/skills/hallmark`); fix FAIL lines, report REFLEX and judged gates in the PR notes.
- [ ] **Step 3:** Check best-effort recolour of other owners' files (`TeamManagement`, `HrAccess*`, `Hr*`, `SubscriptionsWorkspace`, `pm/`) in Night and Bureau by screenshot; list residual literals (file:line) for the PR; edit none of them.
- [ ] **Step 4:** Run, one file per invocation: all changed Python tests, `tests/test_packaged_console.py` is CI's job (do not run locally), web lint, typecheck, `npm test`, `make lint`, `python -m scripts.pixelate_avatars --check`.
- [ ] **Step 5: Commit** `chore(console): theme and responsive fixes from verification`.
- [ ] **PR notes (controller, after the final review):** the shared `AgentCard` also restyles the unlinked `agents` pages; new migrations 0501 and 0502 with rollout order (apply via the migrate one-shot, then recreate gateway, worker, delivery and frontend together); the icon rail change to nav tests; the residual literals list; per-theme best-effort files; Hallmark skill is untracked and not part of the PR.
