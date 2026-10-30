# Console redesign phase 2b: full-screen shell, assistant home, loading screen, simple auth pages, in-shell password

Date: 2026-10-30. Same branch as phase 2 (`feat/console-redesign-phase-2`, unpushed): new commits on top, a follow-on to `docs/superpowers/specs/2026-10-29-console-redesign-phase-2-design.md`, which stays binding for everything not changed here. Rules: `.claude/prompts/_common-context.md`.

## 1. Intent

After trying the phase 2 preview, the owner asked for: the dashboard to fill the screen; the collapse control first in the navigation; an Overview that is either useful or the sandbox assistant in a ChatGPT-like layout (picked: **B, the assistant**); a loading screen of five walking agents with "Loading" below (picked: **treadmill**); simpler, more vibrant sign-in and account pages with a big FourStacks logo on a cobalt left side and the old wording removed (picked: **layout 3, logo plus one short line**); and a Password page inside the dashboard, while the separate full-screen password page serves the forgotten-password flow. They also asked for a better layout for every tab. Mockups: the visual-companion page "Your feedback: proposals".

## 2. Scope

In this round
- Shell fills the window (no outer padding or rounded outer card); the collapse/expand toggle is the first element of the rail and first in tab order.
- Overview becomes the assistant home (ChatGPT-like), built from the existing sandbox assistant; the Sandbox assistant nav entry folds into Overview.
- Loading screen (treadmill) used for route-level loading.
- Auth pages (sign-in, forgot password, set new password, and every page that renders `AuthCard`) restyled: cobalt left panel with a big logo and one line, simple right form.
- In-shell Password page; Password nav entry points to it; `/change-password` stays for the forced change and the reset flow.
- Slogan options supplied to the owner (section 8).

Not in this round (agreed order for later rounds, one plan each): per-tab layouts in the order Runtime activity, Knowledge base, Email assistant, Business management, Subscriptions, Tenants; People & HR (scheduled phase), Members and Team (lane B files).

## 3. Non-goals and anti-goals

- No backend change and no migration. No new routes beyond two in-shell password pages. No new roles or permissions.
- Do not edit lane B files (`TeamManagement.tsx`, `InvitationList.tsx`, `csa/console/identity.py`, the invite acceptance page under `app/(auth)/invite/`, HR access files) or `Hr*.tsx`. The auth look reaches the invite pages through `AuthCard` only.
- The assistant must not promise what it cannot do. The sandbox assistant answers knowledge and product-information questions only (no customer records, orders, bookings, tickets, invoices, payments, email or staff escalation, no external sends). Suggestion chips and every line of copy must stay inside that, and chips only fill the composer; nothing is sent by a click.
- No fake or placeholder figures. A failed load never shows as zero.
- Animation never stands for state: the loading screen is decoration beside the word "Loading", and honours reduced motion and the pause preference.

## 4. Constraints

All phase 2 constraints stay (stable `data-testid`s, five widths without overflow, three themes at the contrast ratios, keyboard and focus, reduced motion, tokens only, no secrets, Next.js docs read before touching layouts or `loading.tsx`, web checks and one test file per run on the shared host). New `lib/` and test files go into `scripts/check-isolated.mjs` (both the staged list and the commands list). The auth left panel is the brand cobalt in every theme; the right side follows the active theme.

## 5. Approaches (picked in bold)

Overview chat
- A. Copy `SandboxWorkspace` into a new Overview layout. Duplicates about 280 lines of polling, voice and history logic.
- **B. Extract the chat core (threads, turns, polling, voice/dictation, archive, delayed-reply notices) into shared pieces, render it in the new layout, and make the old route render or redirect to the same thing.** One implementation, behaviour and tests carry over.

Loading screen
- A. A Suspense fallback per page. Inconsistent.
- **B. Route-level `loading.tsx` in the `(admin)` and `portal` groups using one `FullPageLoading` component.**

Auth restyle
- A. New auth layout component plus editing every auth page.
- **B. Rewrite `components/ui/auth-card.tsx` in place; every page that uses it (including the lane B invite pages) picks it up without edits.**

## 6. Design

**Shell.** Remove the outer padding and the rounded outer card so the shell fills the viewport at all widths (rail and top bar keep their edges; content scrolls inside). The toggle moves to the very top of the rail, above the brand mark and all links, first in DOM and tab order (`data-testid="shell-rail-toggle"` unchanged). When the rail is collapsed the toggle remains the first item. No change to nav names or testids.

**Overview = assistant home.**
- Layout: history column on the left (private conversations, "New chat"), a centred conversation column, a large composer pinned to the bottom, the assistant avatar in the empty state with "How can I help today?" and a few prompt chips written within the sandbox limits (they prefill the composer only). On narrow screens the history becomes a slide-over.
- Status strip above the conversation, only real data and only where the role and packages allow: replies needing review (`needs_review_count` from the inbox list read) and services active (from the services read). If a read fails or the role cannot use it, the strip is hidden, never zeros. The strip links nowhere new (plain text) or to existing pages.
- Platform administrators keep the tenant selector the sandbox already needs (placed in the page header); tenant members use their active tenant. The old "Workspace administration" Overview content is removed (Tenants and Members stay in the nav).
- All existing sandbox behaviour and copy about limits stays (disclaimer notice, held/queued/failed statuses with their exact wording, archive hides history, voice and dictation, delayed-reply notice). `/chat` and `/portal/chat` redirect to Overview preserving `client_id`; the Sandbox assistant nav entry is removed; the existing sandbox browser/voice tests keep passing against the new location (move or update, listing each change).
- The floating workspace assistant dock is unchanged.

**Loading screen.** `FullPageLoading` shows five agents (support, orders, tickets, bookings, knowledge) in a row at the 48 grid, stepping up and down in a staggered wave over a scrolling dashed ground line, with "Loading" and animated dots below. Uses `AgentAvatar` in a non-animating state with a wrapper doing the bobbing; under reduced motion or the pause preference the agents stand still and the dots do not animate; the word "Loading" is in `role="status"`. At widths under 480 px the sprites use the 32 grid so five fit.

**Auth pages.** `AuthCard`: left panel (hidden below 1024 px, replaced by a slim cobalt banner with the logo) in a cobalt gradient with a faint pixel grid; a large FourStacks mark (layers drop into place once on load, reduced motion shows it static) and wordmark, then one tagline line (a single constant `AUTH_TAGLINE`; default "Your customer team, always on."). All old left-side wording and the list of three claims are removed. Right: title, one-line subtitle, fields, one primary cobalt button, one link; same props as today (`title`, `subtitle`, `children`) so no page changes. The set-new-password, forgot-password, sign-in and invite pages all inherit it.

**Password in the dashboard.** New pages `app/(admin)/password/page.tsx` and `app/portal/password/page.tsx` render a card with current password, new password (at least 12 characters, same rules and the same `identity.changePassword` call as today) and confirmation; on success the page shows a confirmation and stays (no redirect), on failure the server's message. Do not claim that other sessions are signed out unless the backend does (check `change_password` in `csa/console/identity.py`, read only). `navigationFor` points the Password entry at `/password` (admin) and `/portal/password` (portal); the existing `/change-password` page is unchanged in behaviour and keeps serving the forced change (`must_change_password` redirect). How reset links are redeemed lives in the lane B invite work on `main`; this round does not edit it.

## 7. Testing

Node: tests for any pure helper (rail order, tagline constant, navigation entry for Password). Browser (stubbed upstream, as in phase 2): shell has no outer gap at five widths in three themes and the toggle is the first focus stop; Overview (assistant) sends a question and shows a reply, shows the strip with real counts and hides it on failure, platform tenant selector, `/chat` redirect; loading screen renders for a slow route and is still under reduced motion; auth pages at five widths in three themes with all testids; the Password nav entry opens the in-shell page, success and error, and `/change-password` still works. Existing sandbox tests updated for the new location.

## 8. Slogan options for the sign-in left panel

1. Your customer team, always on. (default)
2. Customer care, handled.
3. Small team. Big service.
4. Where your agents get to work.
5. Answers in seconds, care in every reply.
6. Four stacks. One steady team.
Avoid "never sleeps" and "every customer answered": the product does not promise either.

## 9. Calls I made (owner can overrule)

- Sandbox assistant folds into Overview and leaves the nav; its old route redirects.
- The strip shows only two real numbers (replies needing review, services active); "open conversations" is not offered by any existing read, so it is not shown.
- The auth left panel is cobalt in all three themes (brand), not themed.
- Default slogan 1 until the owner picks.

## 10. Left to the builder

Component and file layout, exact copy under `docs/WRITING_GUIDE.md`, the chat component split, skeleton and error states, chip wording within the limits, logo animation timing, how to place the tenant selector, sizes of the loading sprites.
