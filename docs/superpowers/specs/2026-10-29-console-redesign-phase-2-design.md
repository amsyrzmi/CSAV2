# Console redesign phase 2: cobalt shell, themes, pixel agents, Services and agents

Date: 2026-10-29. Branch `feat/console-redesign-phase-2` (from `origin/main` b4fc900). Builds on `docs/superpowers/specs/2026-10-28-console-redesign-design.md` (phase 1, merged). Binding rules: `.claude/prompts/_common-context.md`.

## 1. Intent and why

The owner wants the console to feel like one deliberate product. During this phase's brainstorm they:
- chose the layout language of a reference dashboard (Dribbble shot 27599237, "AI Voice Agent Dashboard"): a rounded app card with a slim icon rail, a top bar, stat cards with a link footer, list cards with dashed dividers and a docked assistant panel, with the reference's grey replaced by cobalt blue (#0047AB);
- asked for **three themes the user can switch between**, which now means three colourways of that one shell (decided 2026-10-29): Cobalt (light, default), Night (dark navy) and Bureau (warm paper);
- replaced the vector agent avatars with **pixel-art avatars**, readable in small spaces, one colour each (eleven hues) and one motion each, with a richer working loop for the selected agent;
- kept the phase 2 items: the right avatar on every service and agent card, activate and suspend that animate only after the server confirms, pending and failed states, the same for per-agent switches;
- asked for the phase 1 follow-ups in the same branch.

Why now: Services is where an owner decides what the assistant can do, and it is the first screen where the agents are the subject. The shell and themes land now because every later phase (assistant dock, Overview, HR restyle) builds on them.

## 2. Goals, non-goals, anti-goals

Goals
- Replace `AppShell` with the cobalt shell on every console route (admin and portal), keeping routes, navigation rules and permissions exactly as they are.
- A theme system: tokens only, three themes, user-switchable, remembered per browser, no flash of the wrong theme on load.
- Eleven pixel avatars (32 px grid minimum size, 48 px grid for cards and detail), each with its own idle motion and a working loop.
- Services and the shared `AgentCard` restyled with avatars and server-confirmed animation.
- Phase 1 follow-ups (section 8).

Non-goals
- No layout differences between themes (the Bureau floor-plan Overview idea is deferred), no new routes, roles or permissions, no billing UI.
- Overview, the assistant dock redesign (phase 3), the HR screens (phase 4), the unlinked `agents` and `projects` pages are not redesigned. They recolour automatically through tokens; nothing else changes in them.
- No fake data: the reference's "Call Volume" chart is not copied, because nothing here measures it. Stat cards show only counts the API really returns.

Anti-goals (a failure even if it works)
- Animation that implies state the server has not confirmed, or that approves, rejects, suspends or activates anything.
- A theme or colour that is the only cue for state: status is always a word.
- An icon-only nav a keyboard or screen-reader user cannot name.
- Avatars that make the dashboard noisy: motion must stay quiet, bounded and pausable.
- Any text below 4.5:1 contrast in any theme.

## 3. Constraints

- Everything in the phase 1 constraints section stays binding: stable `data-testid`s (browser tests and `tests/test_packaged_console.py`), layouts at 320, 390, 768, 1024 and 1440 px without page overflow, reduced motion, offscreen suspension and the pause preference honoured, synthetic data only, no secrets.
- Lane B owns `TeamManagement.tsx`, `InvitationList.tsx`, `csa/console/identity.py`, `contracts.py` (invite parts), the HR access files; HR screens (`Hr*.tsx`) are phase 4. The shell and tokens restyle them from outside; their files are not edited.
- `ServicesWorkspace.tsx` keeps its public behaviour and `data-testid`s; TikTok may reuse its components.
- New `lib/` or test files are added to `scripts/check-isolated.mjs`'s allowlist (`components/console/` is already covered). `public/fourstacks/` holds only public images and the proxy matcher exclusion stays exactly `fourstacks/`.
- Migrations only in 0501 to 0509 and only with the owner's agreement, `EXPECTED_SCHEMA_VERSION` set to the highest applied migration, never FORCE RLS.
- Next.js here is newer than common knowledge: read `node_modules/next/dist/docs/` before writing code that touches fonts, cookies, headers or the root layout.
- Local testing: only the changed files' tests, one file per run, the web app's lint, typecheck and `npm test`, and `make lint`; CI runs the rest.
- The Hallmark design skill is installed (`.claude/skills/hallmark`, untracked, MIT). Its app-screen rules are used as the review lens: tokens only, no tracked-capital labels or eyebrows, no decorative status dots, focus-visible, four states for every data region. Its `sloplint` runs on the finished screens where Node and Chrome allow.

## 4. Approaches

Theme mechanism (picked: B).
- A. Three separate stylesheets swapped at runtime. Simple, but every colour must be written three times and drifts.
- B. One token set: the existing `--fs-*` and bridge tokens redefined per `[data-theme]` on `<html>`. The 26 files that use `ui.tsx` and the phase 1 inbox recolour without edits. **Picked.**
- C. A runtime CSS-in-JS theme provider. Adds a dependency and flicker. Rejected.

Pixel avatar delivery (picked: B).
- A. Static pixel PNG/SVG files used through `<img>`. Smallest, but `<img>` cannot be paused by the app's pause preference or offscreen suspension, and cannot carry per-state looks.
- B. A generated data module of run-length-encoded pixel grids rendered by `AgentAvatar` as inline SVG, with frames switched by CSS steps that the existing motion rules control. **Picked.** Keeps the public props of `AgentAvatar`; the server renders the still frame, so there is no layout shift.
- C. Canvas. Not accessible by default and not server-rendered. Rejected.

Shell navigation (picked: B).
- A. Keep the 240 px labelled sidebar and only recolour it. Does not deliver the chosen design.
- B. Icon rail of 56 px with a user toggle that expands it to labelled (240 px), the choice remembered per browser; under 1024 px the rail becomes a menu button opening the full labelled list. Every item has a visible label on expand, an accessible name and a tooltip on focus and hover. **Picked.** The nav has up to 14 entries, so every entry gets its own icon (today two share one).
- C. Top tabs. 14 entries do not fit. Rejected.

Project shape: one branch, four commit groups, one PR (owner chose "both in one branch"). Order: follow-ups, theme and shell, pixel avatars, Services and agents. If review load becomes too heavy the owner can ask to split the PR; nothing in the order prevents that.

## 5. Themes

- Cobalt (default): shell tint `#e3ebfa`, rail `#edf2fc`, surface white, line `#d4def2`, ink `#0b1b3f`, muted `#5a6a8f`, accent `#0047ab`. The mockup values are starting points; the builder confirms every text and control pair at 4.5:1 (3:1 for large text and control borders).
- Night: dark navy surfaces, pale ink, accent a lighter cobalt (about `#6ea2ff`) because #0047AB fails on dark, amber reserved for a human actor (who changed something), blue for agent activity.
- Bureau: warm paper `#f6f1e7` with navy ink and the same cobalt accent; surfaces off-white; hard 3px offset shadow instead of a soft one; square control corners.
- Status colours (success, warning, danger) are defined per theme and always accompanied by words.
- Switching: a theme control in the top bar (the sun slot in the reference). Selection is stored in a `fs-theme` cookie (not sensitive, `SameSite=Lax`, one year) and mirrored in `localStorage`; the root layout reads the cookie and sets `data-theme` on `<html>` on the server so there is no flash. Default Cobalt. `color-scheme` follows the theme. No account setting and no migration (see decision 3).
- Fonts stay Space Grotesk, Inter and Manrope (phase 1). No pixel font anywhere in the app UI; pixel art is only the avatars and small empty-state art.
- **Token sweep (verified in the code, so "tokens only" is not free).** `ui.tsx` hard-codes `bg-white` in `field` and `secondary`; `bg-white` appears in 44 files; ten shared and phase 1 files carry literal hex colours (`fs/CustomerAvatar`, `fs/StatusLabel`, `fs/SlideOver`, `fs/AgentAvatar`, and `inbox/Thread`, `MessageItem`, `ConversationList`, `ReviewCard`, `CustomerCard`, `Inbox`). Scope: this branch converts literals to tokens in every file the redesign owns (`ui.tsx`, `fs/*`, `inbox/*`, `AppShell`, `ServicesWorkspace`, `RuntimeWorkspace`, the avatar components, `globals.css`), and adds a node test that fails on a new literal hex or `bg-white` in those files. For files owned by someone else (Lane B files, `Hr*.tsx`, `SubscriptionsWorkspace`, `pm/`), nothing is edited: Tailwind 4 reads `bg-white` from `--color-white`, so each theme remaps that variable (and the matching text colour) from outside. Night and Bureau are best effort in those files, checked by screenshot and reported, not guaranteed. Any literal that still breaks a theme there is listed in the PR for its owner.

## 6. Pixel avatars

- Source: the eleven approved avatars (`public/fourstacks/agents/*.svg`) converted by a checked-in script (`ui-guide/scripts/`) that supersamples, snaps to each avatar's own palette, removes orphan pixels and emits the data module. The round-2 grids from the brainstorm (48 px, mode-of-palette) are the starting point. The owner reviews a contact sheet of the hand-tidied sprites before they are wired in (a gate in the plan).
- Colour: one hue per agent, the same navy outline and white eyes for all: support 225 (blue), knowledge 172 (teal), orders 28 (orange), tickets 50 (gold), ecommerce 145 (green), invoices 200 (sky), assistant 268 (violet), hr 330 (pink), email 5 (red), subscriptions 95 (lime), bookings 305 (magenta). Each sprite must reach 3:1 against its tile in every theme; the tile colour varies by theme, not the sprite. The avatar is never the only cue for the agent: its name is always next to it.
- Sizes: 32 px grid at 1x or 2x for rows, chips and the nav; 48 px grid at 2x or 3x for cards and detail. No 16 px grid (unreadable).
- Idle motion, four frames and a one-second loop: support sways, knowledge floats then blinks, orders hops, tickets flutters, ecommerce breathes, invoices stamps, assistant bounces like a drop, hr nods, email wiggles, subscriptions orbits, bookings wiggles its ears.
- Working loop: a second frame set per agent (six to eight frames) with one small pixel prop tied to the job (ticket stack, order card, invoice, calendar tick, renewal dial, name cards, two trays, proposal card). It shows only on the selected agent in a detail view and during the wake on confirmed activation; never in lists.
- Motion rules (my call, the owner can overrule): list avatars of active services idle-animate slowly; requested, available and suspended ones are still and dimmed; everything is still under `prefers-reduced-motion`, when the user's pause preference is on, and when offscreen (IntersectionObserver); a page animates at most the idle loops of visible active rows. Server work is never paused by these preferences.
- `lhdn_agent` (E-invoicing) has no avatar: a plain icon and the word "Planned".
- `AgentAvatar`'s existing props keep working; it gains optional `state` (`idle`, `still`, `pending`, `working`, `wake`). `lib/agent-avatars.ts`'s mapping stays. Pixel art needs whole-number scaling, so sizes become 32 and 64 (32 grid at 1x and 2x) and 48, 96 and 144 (48 grid at 1x, 2x, 3x). Existing callers at 24 move to 32; 96 stays valid. `CustomerAvatar` (initials, not pixel art) keeps its own sizes. `public/fourstacks/agents/*.svg` is replaced by pixel versions only if something still needs files, otherwise removed with the proxy exclusion unchanged.
- The conversion script is regenerated from the approved SVGs and committed under `ui-guide/scripts/` (the brainstorm grids lived in a scratchpad). The builder draws the working-loop frames and props; the owner reviews them in the same contact-sheet gate.
- Sprite gate: theme, shell and Services are built against the generated sprites. The owner's contact-sheet review happens before the PR opens; hand-tidying changes only the data module, so it never blocks the other work.
- The pause preference gets a control: a "Calm motion" toggle in the top bar user menu, calling the existing `setMotionPaused` (`data-testid="shell-motion-toggle"`), because nothing in the UI sets it today.

## 7. Services and agents

- Page layout follows the mockup: top bar title, tenant selector (platform admin), four stat cards from real counts (active services, agents on, waiting for activation, suspended), then a list card of services grouped Base, Add-ons and Standalone with dashed dividers. Each row: avatar chip, title, summary, agent count, status word, one action; "Show agents" expands the agent rows inside the card.
- Package to avatar: customer_support to support, orders_products to orders, subscriptions to subscriptions, bookings to bookings, marketplaces to ecommerce, hr to hr, staff_email to email, e_invoicing to icon and "Planned". Agent rows use their own avatar: kb_agent knowledge; ticket_agent and escalation_agent tickets; order_agent and order_approval orders; invoice_agent invoices; ecommerce_agent ecommerce; subscription_agent subscriptions; booking_agent bookings.
- Confirmed-only animation: the avatar changes only after the API call resolves with the new state. While the call is in flight the row shows a pending label and a still, dimmed avatar; on success activation plays a short wake (working loop for about 1.5 seconds, then idle) and suspension settles to still and dimmed; on failure the row keeps its old look and shows an error with the server's reason. The same for per-agent switches. No animation fires from a click alone, and none ever approves anything.
- Per-agent switches keep today's call, `PATCH /console/v1/capabilities/<name>` through the existing `AgentCard` flow in `RuntimeWorkspace.tsx` (the `/api/client/agents` calls belong to the unlinked pages and are untouched). The stat cards compute from `Service.status` and `Service.agents[]`; no new endpoint.
- Keep every `services-*` testid and the existing confirm forms (activate needs a reference; suspend takes a note). `AgentCard` in `RuntimeWorkspace.tsx` is restyled; the unlinked `agents` pages use it and change slightly (to be noted in the PR).
- "Last changed" line: see decision 2. Data check done: a service row stores `requested_by/at` and `activated_by/at`; suspension and capability changes are only in `console_audit` (written by `services.py` and `capabilities.py`); the existing UI says "by a platform administrator" on purpose.

### Shell details

- The rail carries the navigation and the brand mark; the top bar carries the page title, the theme control, the notifications slot is omitted (nothing to show), and a user menu holding the name, role, "Calm motion" toggle and a button named "Sign out". The home link (admin `/`, portal `/portal`) is the brand mark.
- Collapsed links keep their exact existing label as visually hidden text, so `get_by_role("link", name=..., exact=True)` and `get_by_role("navigation", name="Main navigation")` in the browser tests keep working. Where a test cannot work unchanged (for example a visible-text assertion), it changes in this PR with the reason.
- Default on first load: collapsed at 1024 px and wider, a menu button below 1024. The expanded or collapsed choice is stored in an `fs-rail` cookie read by the server together with `fs-theme`, so neither flashes.
- The login, invitation, reset and change-password screens have no shell; they only recolour through tokens and are checked in the three themes. "Every console route" means every route rendered by `AppShell`.

## 8. Phase 1 follow-ups

1. Inbox list query cost: see decision 1 (migration 0501).
2. Phone search: normalise both sides so a stored `0125550148` is found by `+60125550148` and by `0125550148`; add a test.
3. Phone layout: focus returns to the right place after Back on the phone layout; add `scale` to the transition list in `ui.tsx`.
4. Tests: harden two weak assertions in `tests/test_inbox_redesign_browser.py` (focus must be inside the dialog while open and not on the details button; the masked-number check reads the list's `outerHTML` and asserts the thread lacks the full number); add a stubbed browser test for the "Approval expired." branch.

## 9. Testing

- Web: node tests for the theme cookie helpers, the avatar data (every agent has frames, palette within bounds, 3:1 against the tile in each theme), the motion gating, nav entries unique icons and accessible names; keep `nav-visibility`, `agent-avatars`, `services-api`, `motion` tests passing.
- Browser (Playwright, stubbed upstream as in phase 1): Services transitions (request, activate, suspend, agent switch, each with delayed success and failure), theme switch persisted across reload with no wrong-theme flash, shell at the five widths, keyboard nav of the rail.
- Python: the migration test and `tests/test_health.py` / `tests/test_migrate.py` for 0501 if approved; the inbox list and search tests for the follow-ups.
- Visual check against the synthetic preview stack (scratchpad stub gateway) at 320, 768 and 1440 px, in all three themes.

## 10. Owner decisions (approved 2026-10-29)

Approved by the owner: 1 as recommended, 2 option B, 3 as recommended. The migration (0501) is therefore agreed.

- Decision 2 detail (option B): a read-only, additive `last_change` object on each service (and agent capability) returned by the existing services and capabilities reads: `{action, at, actor}`, taken from the newest matching `console_audit` row (`service.requested`, `service.activated`, `service.suspended`, and the capability toggle actions). `actor` is a display name for platform administrators only; for every other role the API returns a role label ("a platform administrator") and no name or id. No new route. **Correction found while planning:** `csa_app` can only INSERT into `console_audit` and cannot read `console_users` (migration 0270), so this needs migration `0502`: a narrow `SECURITY DEFINER` function `console_last_change(client, target, actions)` granted to `csa_app` that filters by tenant explicitly and returns only action, time and actor name. It needs the owner's confirmation before it is built; if declined, option A applies instead. Tests for both audiences.

1. **Inbox list cost (needs a migration).** Recommend three indexes in `0501` (`orders(contact_id)`, `subscriptions(contact_id)`, `approvals(conversation_id)`): additive, reversible, no change to write paths. The alternative, a maintained `conversations.last_activity_at`, changes every place that writes a message and is riskier.
2. **"Last changed" line.** Recommend A: use only the fields already on the service row ("Requested 27 Oct", "Activated 20 Oct", with "by a platform administrator" as today), no backend change, and no date for a suspension because the row does not store one. Option B adds a read-only field from `console_audit` so suspensions and agent switches also show when and by whom; that is a Python task (`services.py`, `contracts.py`, a query, access rules, tests) and a named person shown to tenant members would be a new exposure, so B would show names to platform admins only.
3. **Where the theme is stored.** Recommend per browser (cookie), no migration. Per user across devices needs a `users` column in 0502.

## 11. Calls I made

- Themes are colourways only (decided by the owner on 2026-10-29); Bureau's floor-plan Overview is deferred.
- Idle motion in lists, working loop only in detail and on wake (section 6).
- Stat cards on Services only; Overview is untouched until its phase.
- The assistant panel in the mockup is direction only; the real dock stays as is until phase 3.
- Sprite review is a gate before the PR opens, not before the other work starts (section 6).
- The rail defaults to collapsed with tooltips and an expand control; the pause toggle lives in the user menu.
- Per-theme remapping of `--color-white` is the way to reach files this branch may not edit (section 5).
- Reviewer answers, 2026-10-29: the binding rules file and the Hallmark skill live in the main checkout (`/home/amsyrzmi/CSAV2/.claude/`), not in this worktree, because `.claude/` is untracked there; they are the same files.

## 12. Left to the builder

Component and file layout, exact token values (within the contrast rules), the nav icon choices (all from the existing `lucide-react` set), tooltip implementation, the data module's encoding, frame timing within the one-second loop, skeleton counts and the exact pending and failure copy (under `docs/WRITING_GUIDE.md`).
