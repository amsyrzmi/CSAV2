# Console redesign: FourStacks theme, avatars and the customer inbox

Date: 2026-10-28. Owner: Amsyar. Status: approved by the owner on 2026-10-28, with the five changes folded in below.

This document covers the whole redesign at the level of decisions and phases, and phase 1 (the foundation pieces the inbox needs, plus the inbox itself) in build detail. Later phases get their own short design addendum, with mockups, before their plan is written.

## 1. Intent

The console should look and feel like FourStacks: the light blue theme in `ui-guide/`, the white-eyed blue agent family, and quick, purposeful motion. Today the console has the colour tokens but not the fonts, no avatars, and a customer inbox that identifies a conversation by an 8-character ID and a channel name. Staff cannot tell who they are talking to.

Why now: the inbox is the screen staff use all day, WhatsApp is about to give it real traffic, and the redesign is also where the writing guide (program item B3) gets applied.

## 2. Goals, non-goals, anti-goals

Goals
- Apply the `ui-guide/` identity (light theme, `--fs-*` tokens, Space Grotesk / Inter / Manrope, blue family) to every finished console tab.
- Make the customer inbox readable: who the customer is, which channel, who owns the conversation, what needs review.
- Show the right agent avatar wherever an agent appears, and add five new avatars for agents that have none.
- Add motion that explains state (arrival, takeover, delivery, activate and suspend) and honours reduced motion.
- Apply `docs/WRITING_GUIDE.md` to the words on every restyled screen (program item B3).

Non-goals
- No new routes, roles, API behaviour changes beyond the additive read fields in section 7, or changes to authentication, tenant isolation or permissions.
- No billing or payment UI.
- No streaming assistant replies (a later backend follow-on).
- No restyling of unfinished or in-progress tabs (section 5).

Anti-goals (a failure even if it "works")
- A failed load that looks like "no conversations", or any fake metric, simulated progress or placeholder data shown as live.
- Colour as the only cue for customer versus new sender, or for state.
- An animation that approves, rejects, takes over or releases anything. Those stay explicit, permission-checked buttons.
- An agent avatar that implies a capability that is not built.

## 3. Constraints (binding)

- Keep unchanged: routes, API requests, authentication, tenant isolation, server-side permissions, role rules. No new console roles.
- Keep every `data-testid` stable. The browser tests (`tests/test_*_browser.py`, `tests/test_packaged_console.py`) depend on them. Any test that must change changes in the same PR, with the reason in the PR.
- Layout works at 320, 390, 768, 1024 and 1440 px with no page overflow; long customer names never break a row.
- Keyboard, visible focus and contrast per `UI_GUIDE.md` section 11. Reduced motion, offscreen suspension and a user pause preference are honoured; pausing decoration never pauses server work.
- Develop and test only against the isolated dev stack and disposable databases with synthetic fixtures. No secrets in code, docs, logs or fixtures.
- Migrations: only in range 0500 to 0509, only with the owner's agreement, `EXPECTED_SCHEMA_VERSION` in `apps/gateway/main.py` an exact match, never FORCE RLS. Other branches in flight: HR access (B2) adds 0482 and TikTok Shop uses 0490 to 0499. Whichever branch merges last sets `EXPECTED_SCHEMA_VERSION` to the highest applied migration. If 0500 reaches staging before TikTok, TikTok must renumber or bump its expected version to match; the order is checked at each merge, not assumed.
- Next.js in this repo is a newer version than common knowledge; read `node_modules/next/dist/docs/` (for example `01-app/03-api-reference/02-components/font.md`) before writing code that uses it.
- Local testing: only the tests for changed files, one file per run, plus the web app's own lint, typecheck and tests and `make lint`. The full suite and the slow packaged-console tests are the PR's CI job. Every repo test or lint command needs `NO_LOCAL_ENV=1 ENV=ci`.

## 4. Approaches considered and what the owner picked

How to build the foundation (picked: B).
- A. Restyle in place: change `components/console/ui.tsx` and `globals.css`, build the inbox on them. Smallest change, but class strings cannot carry behaviour (avatars, skeletons, the slide-over, motion), so each screen would invent its own.
- B. Restyle in place plus a small set of new building blocks: `AgentAvatar`, a customer initials avatar, status labels, skeletons, a slide-over built on native `<dialog>`, and shared motion rules. `ui.tsx` stays the single import for the 26 files that use it, so they do not change. `components/ui/kit.tsx` (used by the unfinished `pm/` tabs) is left alone. Larger first PR, much shorter later phases. **Picked.**
- C. Adopt a component library for dialogs and drawers. Adds exact-pinned dependencies and a second styling system to solve what native `<dialog>` already solves here. Rejected.

Inbox layout (picked earlier: three panes). List, thread, and a customer card. A "Needs review" filter replaces the separate approvals section.

Palette (picked this session). Blue throughout, taken from the avatars' own palette; only status colours (amber for needs review, green for success, red for failure) stay non-blue, always with words.

## 5. Which tabs are in scope

| Status | Routes (admin and portal) | Plan |
|---|---|---|
| Finished, restyle | Overview, Services, Runtime activity, Knowledge base (`/faq` on the portal), Sandbox assistant, Customer inbox, Email assistant, Business management, Subscriptions, Tenants, Members, Team, Password, login and invite screens, the assistant dock | Restyled in the phases below. |
| Held | People & HR (`Hr*.tsx`, B2, in `feat/hr-access-page`), TikTok Shop (not on `main`) | Untouched until B2 and TikTok merge; restyled afterwards. |
| Unfinished, not touched (owner, 2026-10-28) | `projects/*`, `agents`, `agents/[name]`, `portal/projects`, `portal/shared-projects` | Left unstyled. |
| Not real pages | `system` (always 404), `workspace` (redirects) | As is. |

Side effect to note in the PR: `AgentCard` lives in `RuntimeWorkspace.tsx` and is shared by Services and the unlinked `agents` pages, so restyling it also changes the unlinked pages slightly.

## 6. Phases (each ships as its own PR with its own plan)

1. **Foundation pieces plus inbox** (this document, section 7). Fonts, avatar mapping and assets, motion and loading primitives, restyled `ui.tsx`, the new inbox, additive inbox read fields, the Telegram username migration (0500, subject to the owner's review of section 7.6).
2. **Services and agents.** Avatars on service and agent cards; activate and suspend animate the avatar only after the server confirms; per-agent switches the same; pending and failed states shown.
3. **Workspace assistant dock.** New floating dock, proper chat with the assistant's avatar, a typing indicator while the reply is pending, and a typed-out reveal of the completed reply with a skip control; shown at once under reduced motion.
4. **Overview** from real API data only, plus avatars in the remaining finished tabs and the rest of the shared foundation.
5. **AI persona** (backend only): friendlier replies in `csa/decide/llm.py` and `csa/workspace/planner.py`, never claiming to be human, never bypassing grounding, respecting the tenant's `brand_tone`, backed by `csa/evals` and the decide tests.
6. **Wording pass completion** and any avatar adoption that is left. B3 wording happens in each phase for the screens it touches; this phase mops up the rest of the finished tabs.

The five new avatars (section 8) are drawn now and adopted by whichever phase first shows that agent.

## 7. Phase 1 in detail

### 7.1 Foundation pieces (only what the inbox needs)

- **Fonts.** Load Space Grotesk, Inter and Manrope through the Next.js font pipeline in `app/layout.tsx` with system fallbacks, and point `--fs-font-*` at them. (Builder: read the font doc in `node_modules/next/dist/docs/`.)
- **Avatar mapping and assets.** One shared mapping from stable service IDs to avatar files: `support`, `knowledge`, `orders`, `tickets`, `ecommerce`, `invoices`, plus the five new `assistant`, `hr`, `email`, `subscriptions`, `bookings`. Console capability names map onto it in one place: `kb_agent` to `knowledge`, `ticket_agent` and `escalation_agent` to `tickets`, `order_agent` and `order_approval` to `orders`, `invoice_agent` to `invoices`, `ecommerce_agent` to `ecommerce`, `subscription_agent` to `subscriptions`, `booking_agent` to `bookings`; the customer-reply agent is `support`. `lhdn_agent` has no avatar: a Lucide icon with a "Planned" label. Phase 1 ships all eleven static body-only SVGs: the six from `ui-guide/assets/agents/` and the five new ones (section 8), which are added to `ui-guide/assets/agents/` and `manifest.json` in this PR and copied to `public/fourstacks/agents/`. `AgentAvatar` (from `ui-guide/examples/AgentAvatar.tsx`) gets the new IDs. The animated engine and the new agents' working poses are phase 2.
- **Customer avatar.** Round initials in one of four blue tints chosen by a stable hash of the contact id, so a person always keeps their colour. No real name (none stored, or only the contact's own number): a dashed circle with "?". The avatar shows who the person is; the label (section 7.3) says whether they are a known customer or a new sender, so a named new sender still gets initials. Agents are never round: they are the creature on a rounded tile with the silhouette uncropped. Shape first, colour second, so customer and agent cannot be confused.
- **Status label.** One component for the guide's presentation states (neutral, info, attention, success, error) with text always present.
- **Loading.** A skeleton primitive shaped like the row it replaces; a shimmer that stops under reduced motion. Loading, empty, unavailable (failed), partial and not-allowed are five different presentations (mockup 2026-10-28).
- **Motion.** One module with the product curves and durations from `UI_GUIDE.md` section 9 (120 to 180 ms feedback, 180 to 240 ms panels), a `useMotionAllowed()` that is false under `prefers-reduced-motion`, a hidden tab, offscreen content or the user's pause preference, and the pause preference stored in the browser (no server call).
- **Slide-over.** A reusable panel on native `<dialog>`: focus moves in, Escape and Close return focus to the opener, scroll lock, no focus trap library.
- **`ui.tsx` restyle.** `action`, `secondary`, `field`, `Notice`, `Heading`, `Panel` pick up the 6 px control radius, 14 px panel radius, 40 px controls (44 px touch target where needed), Space Grotesk headings, blue focus ring. Same names and signatures, so the other 25 files are unchanged. `components/ui/kit.tsx` and `components/pm/` are not touched.

### 7.2 Inbox: structure

- Three panes at 1100 px and above: conversation list (about 230 px), thread, customer card (about 190 px). At 1024 px and below the card folds into a "Details" button that opens the slide-over; on a phone the list and thread are separate full-width views with a back arrow, and "Details" opens the slide-over.
- **List.** A search box above the chips (name, Telegram @username or phone; section 7.5), then filter chips: All, Needs review (with a count that is always visible), Customers, New senders; plus the channel filter (keeps `data-testid="inbox-channel-filter"`). Rows: avatar, name, secondary line (Telegram @username or WhatsApp profile name or masked phone, plus channel), last-message preview, relative time, labels (Customer or New sender, owner, Needs review). The channel badge keeps `data-testid="inbox-channel-badge"`.
- **Thread.** Day dividers, bubbles with grouping and timestamps, delivery text from `lib/channels-api.ts` (kept, with its notices and `data-testid="inbox-delivery-notice"`), channel badge in the header. Customer messages on the left; the replying agent's avatar and a blue-edged bubble on the right; staff replies as dark-avatar "Team" bubbles in deep blue. The held reply is an inline review card with Approve, Edit and Reject, still going through the existing proposal flow.
- **Customer card.** Contact details, the conversation's owner, and that conversation's approvals, read from the existing approvals route scoped by `conversation_id` (the SQL already supports it). It keeps the "Include handled approvals" checkbox with exactly that label, because `tests/test_packaged_console.py` uses it, so handled and expired approval history stays reachable. The tenant-wide "Reply approvals" panel goes away; pending approvals also appear in the thread and under the "Needs review" filter. An approval still `pending` but past its expiry is shown read-only as expired.
- **Takeover and release** keep today's confirmations and behaviour; the room changes (section 7.4) only after the server confirms.

### 7.3 Customer versus new sender

- **Known customer (owner chose "not a buyer yet", 2026-10-28):** the contact has at least one order, at least one subscription, or an email or address on file. **New sender:** none of those. Derived from existing columns and the `orders` and `subscriptions` tables, which already reference `contact_id`; no new column. A sender who has a name or a phone but nothing else is therefore a New sender.
- Shown as a text label and a filter, and by avatar shape and tint; never colour alone.
- `contact_kind` values in use are `customer` (every real contact) and `client_user` (sandbox contacts the inbox already hides). No staff or test kind exists, so no such label is drawn; the label slot supports a new kind if one is added.

### 7.4 Engagement (all six picked)

1. Last-message preview and relative time in the list (needs the read field in 7.5).
2. New bubbles ease in; if the reader has scrolled up, a "2 new messages" pill returns them to the bottom without moving the view. The existing 3-second refresh stays.
3. The Needs review badge nudges once when the count rises, never loops.
4. J and K move between conversations, Escape closes the card. The shortcuts are ignored while focus is in the composer or any input, textarea, select or editable field (including the search box), when a modifier key is held, and while a dialog or slide-over is open. Navigation only: approve, edit and reject never get a shortcut.
5. Takeover changes the room: the header takes a dark accent and the composer slides up when the server confirms takeover, and reverses on release. Reduced motion: the change is instant.
6. Delivery state fades between queued, sent, delivered and not delivered with text labels, driven only by the real delivery state.

### 7.5 Backend: additive read fields (no behaviour change)

`GET /console/v1/inbox/conversations` and `.../{id}` return, per conversation, in addition to today's fields:
- `contact`: `display_name` (the stored name or null), `telegram_username` (null until 7.6 lands and the customer writes again), `phone` (null when unknown), `kind` (`customer`).
- `last_message`: `preview` (first 120 characters of the redacted body, the same text the thread shows) and `at`.
- `pending_approvals`: count of pending approvals for that conversation.

`display_name` is null when the stored name is only the contact's own number (WhatsApp stores `+<number>` as the name when the customer has no profile name); the UI then shows the masked number as the row's name, and the full number only in the customer card.

The list is ordered by last message time, newest first, with a keyset cursor on `(last_message_at, id)` computed with a lateral lookup on `messages` (no migration), so a conversation with a new reply rises to the top and the preview time is never out of step with the order. A conversation with no messages falls back to `opened_at`. The cursor is opaque to the web client, as today.

`needs_review` means a pending approval whose `expires_at` is in the future; the count, the filter and `pending_approvals` all come from that one definition.

The list also accepts three optional filters, applied on the server so they cover all pages and not only the loaded page: `needs_review=true`, `sender=known|new` and `q`, a search over the contact's name, Telegram @username and phone. `q` is trimmed, needs at least two characters, is capped at 64, is matched case-insensitively as a substring with `%`, `_` and `\` escaped, ignores a leading `@`, and matches phone numbers on digits only (so `012 555`, `+60125550148` and `0125550148` agree). It never searches message bodies. The search text is personal data: it is not written to the audit log or application logs, and the builder must confirm the gateway's request logging does not record query strings or else send it in a way that is not logged. No migration or index is added; tenants are small, and a trigram index is a later change if a tenant outgrows it. The search box debounces typing, and the results count is the server's. All three filters combine with the channel filter. The response also returns `needs_review_count` for the whole tenant so the chip count is the server's. Roles are unchanged (platform admin, tenant admin, tenant staff). Phone numbers and usernames are shown only to those roles; list rows show the phone masked to its last four digits, the customer card shows it in full. Message bodies stay redacted as today. The OpenAPI snapshot is regenerated, and `docs/CONSOLE_API.md` documents the new fields.

### 7.6 Migration proposal for the owner to review: `0500_contact_telegram_username.sql`

```sql
-- Telegram @username, captured from incoming messages. Nullable: most people have none.
ALTER TABLE public.contacts ADD COLUMN telegram_username text
  CHECK (telegram_username ~ '^[A-Za-z0-9_]{4,32}$');
```

- `resolve_telegram_contact.sql` also writes the sender's username on every incoming message when it differs (so a changed username stays current); the Telegram event model gains an optional `username` read from `message.from.username`. Existing conversations show a username only after the customer's next message; there is no backfill.
- `EXPECTED_SCHEMA_VERSION` becomes `0500`. Rollout follows the same order as 0480 and 0481: apply with the `migrate` one-shot, then recreate gateway and worker straight away, because images older than the migration fail the exact version check.
- No RLS change (column on an existing table). A Telegram @username is personal data: it is returned only on the staff inbox routes above, never in logs or the assistant's context.
- WhatsApp needs no change: the profile name is already stored in `full_name` and the number in `phone`. Telegram rarely shares phone numbers, so phones will mostly appear on WhatsApp.
- Username capture must never block a message (owner, 2026-10-28). The username is validated against the same pattern in the ingress code, and the SQL writes NULL for anything that does not match, so a username that Telegram sends but the check would reject stores NULL and the contact update, the conversation and the message all succeed. A test sends an invalid username (too short, odd characters, a non-string) and asserts the message is accepted and `telegram_username` is NULL.
- 0500 and its ingress change are the last, separable task of phase 1. Approving this document approves the migration as shown; if the owner would rather defer it, phase 1 ships without it and the username line appears when 0500 ships.
- Merge order with other branches: HR (B2) and TikTok take their own ranges and touch other files. This PR changes `ui.tsx`, `layout.tsx` and `globals.css`, which they import but do not edit, so it is rebased on whatever merges first and the exact schema version is re-checked at merge.

### 7.7 States and copy

Five distinct states, in plain words per the writing guide: loading (skeletons); empty ("Conversations from your customers will appear here. Connect WhatsApp to get started." The "Connect WhatsApp" button, which goes to Services, shows only to roles that can connect a number, which today are platform administrators and tenant administrators, the same rule the Services tab uses; tenant staff see "Ask your administrator to connect WhatsApp." and no button); unavailable ("We could not refresh your conversations." with "Try again", old rows kept); partial ("Showing the latest conversations only." with "Load older conversations"); not allowed ("You do not have access to the customer inbox. Ask your administrator to give you staff access.", no retry). Button labels start with a verb; sentence case; Malaysian English; dates as "27 Oct 2026, 09:15". Navigation labels follow program item B3 for the tabs touched.

### 7.8 Verification for phase 1

- Web: `npm run lint`, `npm run typecheck`, `npm test`, `npm run check:isolated`; new node tests for the avatar mapping, tint hash, label derivation, motion gating, the state components, the J and K handler (ignored in inputs, with modifiers and with a dialog open) and the role-dependent empty state.
- Backend: focused tests for the new read fields, filters and counts (`tests/test_console_inbox.py`), the search filter (case, `@`, phone formats, escaping, short and over-long input, and that results stay tenant-isolated), the username capture including the invalid-username case (ingress tests) and the migration (`tests/test_migrate.py`, `tests/test_health.py`); `make lint`; `docs/console_openapi.json` regenerated as the last commit.
- Browser: the existing inbox and packaged-console tests must pass with their `data-testid`s unchanged; add one inbox browser check at 390 and 1440 px. These run in CI.
- Manual: synthetic fixtures for the unknown-sender state (staging has none), checked at 320, 390, 768, 1024 and 1440 px, keyboard-only, and with reduced motion.

## 8. The five new avatars (approved by the owner, 2026-10-28)

Drawn in the family's recipe (blue gradient body, shade layer, accent band, tapered tendrils, white eyes in a dark well; no mouths): `assistant` (beacon, 2 stacked eyes), `hr` (clover), `email` (swallow, 3 eyes in an arc), `subscriptions` (loop, an open crescent), `bookings` (tabs). The static SVGs are added in phase 1 (section 7.1); their working-pose (animated) versions are designed in phase 2. The draft generator lives in the session scratchpad and is added under `ui-guide/scripts/` so the shapes can be reproduced. Eye counts may repeat; silhouettes and eye arrangements are unique.

## 9. Calls I made (owner can overrule)

- Customer avatars use blue tints (owner asked for a blue palette), replacing the earlier warm-tint idea.
- Staff messages show a dark "Team" avatar, not a person's initials, because messages store only `author = 'human_support'`, not which staff member wrote them. Per-person initials would need the acting user recorded on the message, a separate follow-on.
- The replying agent in a thread is `support` for every assistant message, because messages do not name the agent.
- Phone masked to the last four digits in list rows, full in the customer card, for the same roles that can already open the inbox.
- Search covers name, @username and phone only, not message bodies.
- The pause preference is a browser-local setting; it does not exist on the server.
- The inbox list is now ordered by last message time, not opened time (section 7.5). This changes ordering behaviour, not data or permissions.
- "Needs review" counts pending approvals that have not expired.
- An unnamed WhatsApp sender's row shows the masked number as its name.
- Handled approvals stay reachable from the customer card, so the existing browser test keeps working.

## 10. Left to the builder

Component file layout, exact tint values within the blue family (checked for 4.5:1 contrast), the preview length, skeleton counts, the time-ago formatting, test fixture shapes, and the exact shape of the slide-over's open and close animation within the 180 to 240 ms band.

## 11. Running list of suggestions for later phases

- Services: the avatar wakes into its working pose on confirmed activation and settles on suspension; a small "last changed by, on" line from the existing audit data.
- Assistant dock: the assistant's avatar peeks from the dock when a proposal needs the person's confirmation.
- Overview: per-agent tiles from real counts (pending approvals, open conversations, subscriptions due); an honest "not connected yet" tile for services that are active but not set up.
- Inbox follow-on: record the acting staff user on each human reply so the thread can show who wrote it.
- Command palette (Ctrl or Cmd + K) to jump between tabs and recent conversations, navigation only.
- Offline and reconnect banner when the 3-second refresh keeps failing.
