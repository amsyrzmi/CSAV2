# Console Redesign Phase 2b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the shell fill the window with the collapse control first, turn Overview into the ChatGPT-style assistant home, add the walking-agents loading screen, restyle every auth page, and add an in-shell Password page.

**Architecture:** Pure CSS/layout changes in `AppShell`/`Rail`; the existing sandbox chat is restyled in place (`SandboxWorkspace` keeps its name) and rendered by new Overview pages that add a real-data status strip around it; `AuthCard` is rewritten in place so every auth page inherits it; one `FullPageLoading` component serves a root and two group-level `loading.tsx` files; two new in-shell password pages reuse `identity.changePassword`.

**Tech Stack:** Next.js 16.3.8 (read `node_modules/next/dist/docs/` before touching layouts or `loading.tsx`), React 19, Tailwind 4, `lucide-react`, node `--test`, Playwright (python) stubbed-upstream browser tests.

**Spec:** `docs/superpowers/specs/2026-10-30-console-redesign-phase-2b-design.md` (binding; read it and sections 9b of it first). It builds on `docs/superpowers/specs/2026-10-29-console-redesign-phase-2-design.md`.

## Global Constraints

- Web root `CSA_WebConsole/csa-console/web` (WEB). Every repo test or lint command goes through the wrapper `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/44845bab-014a-4610-95de-184b6a6d4038/scratchpad/ci.sh <command>` (no execute bit; worktree sessions refuse `VAR=value` prefixes and compound commands). Python: `/home/amsyrzmi/CSAV2/.venv/bin/…`. Node: `/tmp/claude-1000/-home-amsyrzmi-CSAV2/44845bab-014a-4610-95de-184b6a6d4038/scratchpad/bin/node` (npm is not on PATH: reuse the lint/typecheck wrapper approach in the previous task reports; typecheck needs a dummy `CSA_CONSOLE_API_ORIGIN`). Local testing: only changed files' tests, one test file per invocation, web `lint`, `typecheck`, `node --test tests/*.test.mjs`, `make lint`; check `free -h` first; the full suite and `tests/test_packaged_console.py` are CI's job (read its locators and reason through them).
- Keep every existing `data-testid` stable. New `lib/` and test files go into `scripts/check-isolated.mjs` (staged list AND commands list). Tokens only in owned files (`tests/theme-literals.test.mjs`); no fake figures; a failed load never shows as zero.
- No backend change, no migration, no new roles. Do not edit lane B files: `TeamManagement.tsx`, `InvitationList.tsx`, `csa/console/identity.py`, `app/(auth)/invite/**`, HR access files, `Hr*.tsx`, `docs/PROGRESS.md`. The auth look reaches the invite pages through `AuthCard` only.
- The sandbox assistant answers knowledge and product-information questions only (no customer records, orders, bookings, tickets, invoices, payments, email, staff escalation, no external sends); chips and copy stay inside that; a chip only fills the composer.
- Reduced motion, offscreen suspension and the Calm motion preference are honoured by every new animation (static when blocked); `role="status"` text "Loading" stays.
- Five widths (320, 390, 768, 1024, 1440) without horizontal overflow in the three themes (cobalt, night, bureau); 4.5:1 text and 3:1 non-text contrast in every theme; the auth left panel is the brand cobalt in every theme.
- Commit locally after each task, message ending `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` exactly. Never push, rebase or amend earlier commits.
- Slogan constant `AUTH_TAGLINE = "Your customer team, always on."` (owner may change later); loading agents `support, orders, tickets, bookings, knowledge`; password rule at least 12 characters.

## Review Focus

- Overview for each role and state: platform admin without a tenant (selector, prompt, no composer), tenant member without an active tenant (existing message), read-only member (disabled composer), a tenant without customer_support (chat works, strip shows only what is allowed) (Task 7).
- Status strip: a failed or disallowed read hides only that number, never shows 0 (Task 7).
- The collapse toggle is the first focus stop on desktop and the menu button stays first below 1024 px (Task 1).
- Password page: server error messages (429, 422 same-as-current) shown; success stays on the page and refreshes the session (Task 3).
- Reduced motion on the loading screen and the logo drop; the word "Loading" always present (Tasks 4, 5).
- A narrow phone (320 px): Overview composer clear of the floating dock button; auth pages without the left panel still show the logo (Tasks 4, 7).

---

### Task 1: Shell fills the window; collapse toggle first

**Files:**
- Modify: `WEB/components/AppShell.tsx` (lines about 60-83: outer `div` with `sm:p-3 lg:p-4`, rounded bordered card, `main` wrapper), `WEB/components/console/shell/Rail.tsx` (toggle order), `WEB/app/globals.css` only if a shell rule needs it
- Test: `tests/test_console_shell_browser.py` (extend)

**Interfaces:** `AppShell({ me, packages, children, initialRail? })` unchanged. Produces: the shell root fills `100dvh x 100vw` with no outer padding, margin, border radius or border; the rail and top bar keep their inner edges; document scrolling stays; `main` keeps `mx-auto max-w-6xl p-5 sm:p-8` for ordinary pages and a page may opt out (Task 7 uses a `data-fill` hook, see Task 7). The rail's first child is the expand/collapse button (`data-testid="shell-rail-toggle"`, same labels), above the brand mark and every link, first in DOM and tab order at 1024 px and wider; below 1024 px the menu button (`shell-menu`) remains the first focusable element in the top bar.

- [ ] **Step 1: Write failing tests** `test_shell_fills_the_window_without_outer_gap` (at 1280, 768 and 390 px in each of the three themes: the shell root's bounding box equals the viewport and the first rail item's left edge is 0), `test_collapse_toggle_is_the_first_focus_stop_on_desktop` (press Tab once from a fresh load at 1280 px: `shell-rail-toggle` is focused), `test_menu_button_is_first_focus_stop_on_narrow_screens` (390 px: `shell-menu` focused).
- [ ] **Step 2: Run** `bash …/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_console_shell_browser.py -k "fills_the_window or first_focus" -q`; expect FAIL.
- [ ] **Step 3: Implement** the layout and DOM-order change; keep tooltips, `aria-current`, sr-only labels and cookie behaviour.
- [ ] **Step 4: Run** the whole `tests/test_console_shell_browser.py`, then `tests/test_services_browser.py`, `tests/test_inbox_redesign_browser.py` (layout changes), web lint and typecheck, node tests.
- [ ] **Step 5: Commit** `feat(console): the shell fills the window and the collapse toggle comes first`.

### Task 2: Account details in the user menu

**Files:**
- Modify: `WEB/components/console/shell/UserMenu.tsx` (`ShellAccount` gains `email: string; tenant: string | null`), `WEB/components/AppShell.tsx` (new optional prop `tenantName?: string | null`, passed into the account), `WEB/app/portal/layout.tsx` (fetch the profile with `requireOwnProfile()` when `me.active_client_id` and pass `tenantName`), `WEB/app/(admin)/layout.tsx` (no tenant name)
- Test: `tests/test_console_shell_browser.py` (extend)

**Interfaces:** Produces: the user menu panel shows name, email, role label and (when known) the tenant name as plain text lines; no new testids except `shell-user-email` and `shell-user-tenant`. Never shows anything for the platform admin tenant line.

- [ ] **Step 1: Write failing tests** `test_user_menu_shows_name_email_role_and_tenant` (portal stub: tenant "Synthetic Tenant", email shown) and `test_user_menu_has_no_tenant_line_for_platform_admin`.
- [ ] **Step 2: Run;** expect FAIL. **Step 3: Implement** (the layout fetch must not break the layout when the profile call fails: fall back to no tenant line). **Step 4: Run** the shell browser file, lint, typecheck. **Step 5: Commit** `feat(console): show email and tenant in the user menu`.

### Task 3: In-shell Password page

**Files:**
- Create: `WEB/app/(admin)/password/page.tsx`, `WEB/app/portal/password/page.tsx`, `WEB/components/console/PasswordPanel.tsx`
- Modify: `WEB/lib/navigation.ts` (Password entry href: `to("password")`, i.e. `/password` for admin and `/portal/password` otherwise; key stays `password`), `WEB/proxy.ts` (`adoptedPages` gains `/password` and `/portal/password`), `WEB/lib/nav-*` tests as needed
- Test: `WEB/tests/nav-visibility.test.mjs` (extend), `tests/test_password_browser.py` (create; stubbed-upstream harness copied from `tests/test_services_browser.py`)

**Interfaces:** `PasswordPanel()` (client): fields Current password (`autoComplete="current-password"`), New password and Confirm new password (`autoComplete="new-password"`, `minLength 12`, `maxLength 1024`), the existing `PasswordInput` component, button "Update password"; calls `identity.changePassword(current, password)` then `router.refresh()`; mismatch message "The new passwords do not match."; success shows an `role="status"` message "Password updated. Other devices have been signed out." and clears the fields (stays on the page); errors show the server message in a `role="alert"` (429 throttle and 422 must-differ come from the server). `/change-password` is unchanged.

- [ ] **Step 1: Write failing tests.** Node: the Password nav entry links to `/password` for admin and `/portal/password` for tenant roles. Browser: `test_password_nav_opens_the_in_shell_page` (click Password in the rail: URL is `/portal/password`, the shell is still present), `test_password_change_success_stays_and_confirms` (stub returns `Me`; one POST to `/console/v1/auth/password/change` with `{current_password,new_password}`; confirmation visible; URL unchanged), `test_password_mismatch_blocks_the_request` (zero POSTs), `test_password_server_error_is_shown` (stub 429 and 422 with `detail`), `test_change_password_page_still_works` (`/change-password` still renders its form).
- [ ] **Step 2: Run;** expect FAIL. **Step 3: Implement.** Read `csa/console/identity.py::change_password` (read only) to confirm the "other devices signed out" claim; if it does not hold, drop that sentence. **Step 4: Run** the new browser file, `tests/test_console_shell_browser.py`, node tests, lint, typecheck. **Step 5: Commit** `feat(console): Password opens a page inside the dashboard`.

### Task 4: Auth pages: cobalt panel, big logo, one tagline

**Files:**
- Create: `WEB/components/ui/FourStacksLogo.tsx`, `WEB/lib/auth-copy.ts`, `WEB/tests/auth-copy.test.mjs`
- Modify: `WEB/components/ui/auth-card.tsx` (rewrite in place), `WEB/app/globals.css` (logo drop keyframes, pixel-grid background), `WEB/app/themes.css` only if a token is missing (the cobalt panel colours are fixed brand values in every theme: gradient `#0047ab` to `#2d7cf0`, white and `#7fb0ff` logo layers; put them in dedicated tokens `--fs-auth-from`, `--fs-auth-to`, `--fs-auth-logo`, `--fs-auth-logo-accent`, same value in all three themes), `WEB/scripts/check-isolated.mjs`
- Test: `tests/test_auth_pages_browser.py` (create)

**Interfaces:** `export const AUTH_TAGLINE = "Your customer team, always on."` in `lib/auth-copy.ts`. `FourStacksLogo({ size = 120, animated = true }: { size?: number; animated?: boolean })`: inline SVG of the four stacked layers (path data from `public/brand/fourstacks-mark.svg`), layers drop into place once on mount when `animated` and motion is allowed (CSS keyframes `fs-logo-drop`, staggered 0.25 s), static otherwise. `AuthCard({ title, subtitle, children })` signature unchanged. Layout: at 1024 px and wider two columns; left column the cobalt gradient with a faint pixel-grid, the large logo (size 120), the wordmark "fourstacks" and the tagline line, nothing else (the old eyebrow, headline, three claims and footer text are removed); right column title, subtitle (default none), the form, on the theme surface. Below 1024 px the left column becomes a slim cobalt banner (logo size 40 and wordmark). All existing page markup and testids of login, forgot-password, change-password and invite pages are untouched.

- [ ] **Step 1: Write failing tests.** Node: `AUTH_TAGLINE` equals the exact string. Browser (stubbed upstream; unauthenticated pages `/login`, `/forgot-password`, and `/change-password` and the invite page reached through the stub as the existing tests do): `test_auth_pages_show_logo_and_tagline_and_none_of_the_old_copy` (the page contains the tagline and the wordmark, and does NOT contain "A clear view of your workspace", "Your identity, verified", "Restricted workspace access" or "CSA Console"), `test_auth_pages_fit_five_widths_in_three_themes`, `test_auth_left_panel_is_brand_cobalt_in_every_theme` (computed background of the panel is the same gradient start in cobalt, night and bureau), `test_auth_logo_is_still_under_reduced_motion` (no running `fs-logo-drop` animation), `test_login_form_still_works` (existing testids).
- [ ] **Step 2: Run;** expect FAIL. **Step 3: Implement.** **Step 4: Run** the new file, any existing browser test that loads auth pages (grep `/login` in tests: run each once), web lint, typecheck, node tests. **Step 5: Commit** `feat(console): simple cobalt auth pages with a big logo`.

### Task 5: Loading screen: five walking agents

**Files:**
- Create: `WEB/components/console/fs/FullPageLoading.tsx`, `WEB/app/loading.tsx`, `WEB/app/(admin)/loading.tsx`, `WEB/app/portal/loading.tsx`, `WEB/tests/loading-copy.test.mjs` (only if there is pure logic)
- Modify: `WEB/app/globals.css` (step, ground scroll, dots keyframes; reduced-motion and `[data-motion="off"]` guards), `WEB/scripts/check-isolated.mjs`
- Test: `tests/test_loading_browser.py` (create)

**Interfaces:** `FullPageLoading({ fullScreen = false }: { fullScreen?: boolean })` (client): five `AgentAvatar`s (`support`, `orders`, `tickets`, `bookings`, `knowledge`; state `rest`; size 96 at 480 px and wider, 48 below... note 96 uses the 48 grid at 2x, 48 uses it at 1x) in a row; each wrapped in an element doing a stepped up/down bob (`steps(1)`, 0.5 s, 0.1 s stagger per agent) over a dashed ground line (`repeating-linear-gradient`, scrolling with `steps(4)` 0.6 s); below it the word "Loading" with animated dots (`::after` content steps); `role="status"` with the text "Loading"; `fullScreen` centres it in the viewport on `--fs-bg`, otherwise it centres in its container with `min-height: 60vh`. All motion off under `prefers-reduced-motion`, `[data-motion="off"]` and the Calm motion preference (agents stand still, dots static, the word stays). `app/loading.tsx` renders `<FullPageLoading fullScreen />`; the two group files render `<FullPageLoading />`.

- [ ] **Step 1: Write failing tests** `test_loading_screen_shows_five_agents_and_the_word_loading` (a stubbed slow server-side response, for example a delayed `/console/v1/services/active` on the loopback stub, makes the first load show it; assert five `[data-agent]` sprites and `role=status` text "Loading"), `test_loading_screen_is_still_under_reduced_motion` (animation names none), `test_loading_screen_fits_320_px` (no overflow, five agents visible).
- [ ] **Step 2: Run;** expect FAIL. **Step 3: Implement.** Find a deterministic trigger and say in the report how; if Next cannot show the loading file in the stub, test the component through a tiny dev-only route under the test harness only (never committed to the app). **Step 4: Run** the new file, lint, typecheck, node tests. **Step 5: Commit** `feat(console): loading screen with five walking agents`.

### Task 6: Chat layout (ChatGPT-style) in `SandboxWorkspace`

**Files:**
- Modify: `WEB/components/console/SandboxWorkspace.tsx` (restyle and re-layout in place; keep export name and props `{ me: Me }`; keep all behaviour: threads, turns, polling, voice, dictation, read aloud, archive, delayed-reply notice, status words from `lib/sandbox-chat.ts`), new small files under `WEB/components/console/chat/` as needed (for example `ChatHistory.tsx`, `ChatComposer.tsx`, `ChatEmptyState.tsx`), `WEB/lib/sandbox-chat.ts` (pure helpers only), `WEB/app/globals.css`
- Test: `WEB/tests/sandbox-chat.test.mjs` (extend), `tests/test_voice_browser.py` (update), `tests/test_sandbox_chat_browser.py` (create, stubbed)

**Interfaces:** Layout: a history column on the left (list of private conversations with a "New chat" button; becomes a slide-over opened by a "History" button below 768 px), a centred conversation column (max 48rem) with the message list scrolling inside a fixed-height column, a large composer pinned at the bottom of the column (textarea, send, voice and dictation controls kept), and an empty state (assistant avatar at 56 px, the line "How can I help today?", prompt chips). Chip wording stays inside the sandbox limits (for example "Ask something a customer might ask", "Check what the knowledge base says about a topic", "Rephrase an answer for a customer"); a chip fills the composer and never sends. Disclaimer: the full original Notice text stays visible in the empty state; once a conversation has at least one message it collapses to the one line "Private sandbox: knowledge and product questions only." with a "What it cannot do" disclosure holding the original text verbatim. The page-level `Heading` is removed. Keep accessible names the voice test uses where behaviour is unchanged (Text question, Send to sandbox, Hands-free voice chat); the conversation switcher becomes the history list (update the test, listing each locator change). Pure helpers (extract and test in node): `export function chipsFor(role: Me["role"]): string[]`, `export function disclaimerMode(turnCount: number): "full" | "compact"`.

- [ ] **Step 1: Write failing tests.** Node: `disclaimerMode(0) === "full"`, `disclaimerMode(1) === "compact"`; `chipsFor` returns only the three chips above and none that mention customer records, orders, tickets, email or approvals (assert by regex on forbidden words). Browser (`tests/test_sandbox_chat_browser.py`, stub chat endpoints as `tests/test_voice_browser.py` does): `test_empty_state_shows_prompt_chips_and_full_disclaimer`, `test_chip_fills_the_composer_without_sending` (zero POSTs), `test_send_shows_reply_and_compacts_the_disclaimer`, `test_history_lists_conversations_and_new_chat_creates_one`, `test_read_only_member_composer_is_disabled`, `test_history_opens_as_a_slide_over_on_phone`.
- [ ] **Step 2: Run;** expect FAIL. **Step 3: Implement.** **Step 4: Run** the new file, `tests/test_voice_browser.py` (updated), node tests, lint, typecheck, theme-literals (all new files must be literal-free). **Step 5: Commit** `feat(console): ChatGPT-style layout for the sandbox assistant`.

### Task 7: Overview is the assistant home; the Sandbox tab folds into it

**Files:**
- Create: `WEB/components/console/overview/OverviewHome.tsx`, `WEB/components/console/overview/StatusStrip.tsx`, `WEB/lib/overview-strip.ts`, `WEB/tests/overview-strip.test.mjs`
- Modify: `WEB/app/(admin)/page.tsx` and `WEB/app/portal/page.tsx` (render `OverviewHome`), `WEB/app/(admin)/chat/page.tsx` and `WEB/app/portal/chat/page.tsx` (server `redirect` to Overview preserving `client_id`), `WEB/lib/navigation.ts` (remove the `chat` entry and the `chat` key from `NavKey`), `WEB/lib/shell-state.ts` (`NAV_ICONS`: remove `chat`), `WEB/lib/assistant-view.ts` (the dock's `chat` tab href maps to Overview: `/` for admin, `/portal` otherwise), `WEB/components/AppShell.tsx` (`main` opts out of the centred column for pages with `data-fill`), `WEB/proxy.ts` (`/chat` and `/portal/chat` stay in the allowlist), existing tests that name the Sandbox entry: `tests/test_console_shell_browser.py` (`TENANT_LABELS` and the tooltip neighbour), `tests/test_services_browser.py`, `tests/test_subscriptions_browser.py` (navigation lists), node `nav-visibility`, `shell-state`, `identity-transport`, `assistant-view`, `tests/test_workspace_browser.py` if it references it
- Test: `tests/test_overview_browser.py` (create)

**Interfaces:**
- `lib/overview-strip.ts`: `export type StripInput = { review: number | null; services: number | null }; export function stripParts(input: StripInput): { label: string; value: number }[]` (omits a part whose value is null; labels "Replies needing review" and "Services active"; an empty list means no strip). `StatusStrip({ me, clientId })` (client) reads `inbox/conversations?limit=1&needs_review=...` page for `needs_review_count` only when the role can see the Inbox (not `read_only`) and `customer_support` is active or suspended for the tenant (use `GET /console/v1/services/active`), and counts active services from `GET /console/v1/services/active` for everyone; each failed read gives `null` (never 0); reads pass `client_id` for platform admins.
- `OverviewHome({ me }: { me: Me })` (client): header with the tenant selector for platform admins (writes `?client_id=` into the URL with `router.replace`, seeded from it); states: platform admin without a tenant: avatar plus "Choose a tenant to start chatting", no composer; member without an active tenant: the Notice "Ask a tenant administrator to review your membership."; otherwise the status strip above `SandboxWorkspace` (the strip stays outside the chat component), page marked `data-fill` so it is edge to edge with a fixed-height column (viewport minus top bar) and the message list scrolling inside. Composer reserves right padding below 1024 px so the floating dock button never covers the send button; above it the 48rem column leaves the corner free. A label "Sandbox: knowledge and product questions only" sits in the page header.

- [ ] **Step 1: Write failing tests.** Node: `stripParts` omits nulls, preserves order, empty when both null, never emits a 0 for null. Browser: `test_overview_is_the_assistant_home_for_a_tenant_member`, `test_overview_platform_admin_without_tenant_asks_to_choose`, `test_tenant_selector_writes_client_id_to_the_url`, `test_member_without_active_tenant_sees_the_membership_message`, `test_strip_shows_both_numbers_for_staff`, `test_strip_hides_review_for_read_only_and_when_inbox_read_fails` (the services number stays; a failed read never shows 0), `test_chat_routes_redirect_to_overview_with_client_id`, `test_sandbox_assistant_is_gone_from_the_navigation`, `test_composer_clear_of_the_dock_button_at_390_px` (bounding boxes do not intersect), `test_overview_fits_five_widths_in_three_themes`.
- [ ] **Step 2: Run;** expect FAIL. **Step 3: Implement.** Update every test and node test that named the Sandbox assistant entry or the old Overview content, listing each in the commit message. **Step 4: Run** the new file, each changed browser file once (shell, services, subscriptions, voice, workspace if touched), the whole node set, lint, typecheck, theme-literals. **Step 5: Commit** `feat(console): Overview is the assistant home`.

### Task 8: Verification, preview for every tab, and notes

**Files:** Modify whatever the checks find; update `tests/theme-literals.test.mjs` if new owned files need listing; scratchpad (not committed): `/tmp/claude-1000/-home-amsyrzmi-CSAV2/44845bab-014a-4610-95de-184b6a6d4038/scratchpad/services_stub.py` and `launch_preview.sh`, `pr-notes-phase-2.md`.

- [ ] **Step 1:** Extend the scratchpad stub gateway so EVERY tab renders synthetic data and no page shows "This page is not part of the Services preview.": add synthetic responses for Overview chat (conversations, turns), inbox list and thread, runtime activity runs and overview, knowledge base documents and jobs, email settings and messages, business orders and catalogue, subscriptions, tenants and members, team, HR (read-only lists), `workspace` assistant disabled; follow each page's client calls (grep `api(`/`api<` in its component); 404 only for truly unknown routes with a neutral message.
- [ ] **Step 2:** Restart the preview stack with the current code (stop the old `next dev` and stub first; use the existing `launch_preview.sh`), leave it running at `http://localhost:3100/login`.
- [ ] **Step 3:** Screenshot, view and fix problems: new pages (Overview, Password, loading, login, forgot-password, change-password) and every existing tab at 320, 390, 768, 1024 and 1440 px in cobalt, night and bureau; fix overflow, clipping, contrast and focus issues in owned files; list residual problems in other owners' files.
- [ ] **Step 4:** Run `node scripts/check-isolated.mjs` (full isolated build; heavy: check `free -h`, run in the background) and report the outcome verbatim; run all changed browser files once each, node tests, lint, typecheck, `make lint`.
- [ ] **Step 5:** Append to the PR notes file: the 2b changes, the removal of the Sandbox assistant nav entry, the auth page correction (reset links are redeemed on `/invite`; `/change-password` is only the forced path), the slogan options, and the per-tab layout rounds still to do. Commit `chore(console): phase 2b verification fixes`.
