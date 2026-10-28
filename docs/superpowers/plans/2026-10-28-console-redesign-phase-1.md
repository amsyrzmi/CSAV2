# Console redesign phase 1 (foundation pieces and customer inbox) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the FourStacks fonts, avatar set, motion and loading primitives, and a three-pane customer inbox that shows who the customer is, with search, filters, a Telegram @username and the five engagement features.

**Architecture:** Backend first: additive read fields, filters, search and ordering on the existing inbox routes, then migration 0500 for the Telegram username (a separable last backend task). Front end: pure logic in `lib/*.ts` (tested with `node --test`), new React pieces in `components/console/fs/`, the inbox rebuilt in `components/console/inbox/` behind the unchanged `InboxWorkspace` export, and `components/console/ui.tsx` restyled in place so its 25 other users do not change.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3, PostgreSQL, pytest; Next.js 16.3.8 (newer than common knowledge: read `node_modules/next/dist/docs/` first), React 19.2.8, Tailwind 4.3.3, lucide-react, `node --test`, Playwright (browser tests, run in CI).

**Spec:** `docs/superpowers/specs/2026-10-28-console-redesign-design.md` (read it first; section numbers below refer to it).

## Global Constraints

- Keep routes, API requests, authentication, tenant isolation, server-side permissions and roles unchanged. No new console roles. No billing UI. (spec 2, 3)
- Keep every `data-testid` stable: `inbox-channel-filter`, `inbox-channel-badge`, `inbox-delivery-notice`, and the ones in `tests/test_packaged_console.py`, `tests/test_whatsapp_browser.py`, `tests/test_services_browser.py`. A test that must change changes in the same PR, with the reason in the PR text. (spec 3)
- Layout must work at 320, 390, 768, 1024 and 1440 px with no page overflow; a long customer name never breaks a row. (spec 3)
- Reduced motion, hidden tab, offscreen and the user's pause preference stop decorative motion; pausing never pauses server work. Animation never approves, rejects, takes over or releases anything. (spec 2, 3)
- Migrations only in 0500 to 0509; `EXPECTED_SCHEMA_VERSION` in `apps/gateway/main.py` is an exact match; never FORCE RLS. B2 adds 0482 and TikTok uses 0490 to 0499; whichever merges last sets the expected version to the highest applied migration. (spec 3)
- Copy follows `docs/WRITING_GUIDE.md`: sentence case, verbs on buttons, Malaysian English, "27 Oct 2026, 09:15", no internal codes on screen.
- Synthetic fixtures only; no secrets, no production connections. SQL stays in `db/sql/` and is parameterised; psycopg 3 async only.
- Do not touch `components/console/Hr*.tsx`, HR routes, `components/ui/kit.tsx`, `components/pm/**` or the unfinished routes (spec 5).
- Local testing: one test file per run, through the wrapper `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/44845bab-014a-4610-95de-184b6a6d4038/scratchpad/ci.sh <command>` (it exports `NO_LOCAL_ENV=1 ENV=ci`; the Bash guard refuses `VAR=value` prefixes and `&&`). Docker tests need a clean host first: `docker ps --filter label=org.testcontainers=true`. The full suite and the slow packaged-console tests are CI's job. Run `uv sync --frozen` once in the worktree if `.venv` is missing.
- Commits are local to `feat/console-redesign-inbox`. Never push, never touch `main`.

## Review Focus

Failure modes the spec implies that no single task's happy path covers; each has a test in the named task.

1. An unnamed WhatsApp sender is stored with the name `+60125550148`: the list row shows the masked number as its title, the full number appears only in the customer card (B1, F3, F8).
2. Search text with `%`, `_`, a backslash, a quote or a two-character name such as `Li` never acts as a wildcard, never errors, and never leaks another tenant's contacts (B1).
3. A failed list refresh while rows are on screen keeps the rows and shows the "unavailable" notice, never the empty state (F6, F8).
4. Typing `j` or `k` in the composer, the search box or the edit textarea never moves the selection; Escape closes the slide-over and returns focus to its button (F5, F8).
5. A 60-character name with no spaces at 320 px does not overflow the page or break a row (F8).

## File structure

Backend (create or modify):
- `db/sql/inbox/list_conversations.sql` (rewrite, named params), `db/sql/inbox/get_conversation.sql` (contact fields), `db/sql/inbox/count_needs_review.sql` (new)
- `csa/console/inbox_contact.py` (new: contact model and pure helpers), `csa/console/inbox.py` (models and route params)
- `db/migrations/0500_contact_telegram_username.sql` (new), `apps/gateway/main.py` (`EXPECTED_SCHEMA_VERSION`), `db/migrations/README.md`
- `csa/channels/telegram.py`, `csa/channels/ingress.py`, `db/sql/ingress/resolve_telegram_contact.sql`, `db/sql/ingress/insert_contact.sql`, `db/sql/ingress/get_telegram_contact.sql`, `db/sql/ingress/update_telegram_username.sql` (new)
- `docs/CONSOLE_API.md`, `docs/console_openapi.json` (regenerated), `docs/PROGRESS.md`
- Tests: `tests/test_inbox_contact.py` (new, no Docker), `tests/test_console_inbox.py`, `tests/test_telegram.py`, `tests/test_ingress.py`, `tests/test_contact_username_schema.py` (new)

Front end (under `CSA_WebConsole/csa-console/web/`, called `web/` below):
- `web/lib/agent-avatars.ts`, `web/lib/customer-identity.ts`, `web/lib/motion.ts`, `web/lib/inbox-shortcuts.ts`, `web/lib/inbox-api.ts` (new, pure, no relative imports so `node --test` can transpile them)
- `web/components/console/fs/` (new): `AgentAvatar.tsx`, `CustomerAvatar.tsx`, `StatusLabel.tsx`, `Skeleton.tsx`, `SlideOver.tsx`, `ListStates.tsx`, `useMotionAllowed.ts`
- `web/components/console/inbox/` (new): `Inbox.tsx`, `ConversationList.tsx`, `Thread.tsx`, `ReviewCard.tsx`, `CustomerCard.tsx`, `usePages.ts`; `web/components/console/InboxWorkspace.tsx` keeps its export and path
- `web/app/layout.tsx`, `web/app/globals.css`, `web/components/console/ui.tsx`, `web/public/fourstacks/agents/*.svg` (11 files), `web/scripts/check-isolated.mjs` (register every new file), `ui-guide/assets/agents/` (five new SVGs) and its `manifest.json`
- Tests: `web/tests/agent-avatars.test.mjs`, `customer-identity.test.mjs`, `motion.test.mjs`, `inbox-shortcuts.test.mjs`, `inbox-api.test.mjs` (new); `tests/test_inbox_redesign_browser.py` (new), `tests/test_whatsapp_browser.py` and `tests/test_packaged_console.py` (updated)

---

## Task 1 (B1): Inbox read fields, filters, search and ordering

**Files:** the backend files above except the 0500, telegram and ingress ones; tests `tests/test_inbox_contact.py`, `tests/test_console_inbox.py`.

**Interfaces:**
- Produces, in `csa/console/inbox_contact.py`:
  - `class InboxContact(Contract): display_name: str | None; telegram_username: str | None; phone_masked: str | None; phone: str | None = None; kind: str; known: bool` (`phone` is set only by the single-conversation read; `telegram_username` stays `None` until Task B2).
  - `def mask_phone(phone: str | None) -> str | None` (`"+60 12-555 0148"` gives `"••••0148"`; fewer than four digits gives `"••••"`; no digits gives `None`).
  - `def display_name_or_none(full_name: str | None, phone: str | None) -> str | None` (blank, or only the contact's own number in any punctuation, gives `None`).
  - `@dataclass(frozen=True) class Search: like: str; digits: str | None` and `def parse_search(q: str | None) -> Search | None` (trim; drop one leading `@`; fewer than two characters raises `ValueError`; `like` is `%term%` with `\`, `%`, `_` escaped; `digits` is the digits of the term when it has two or more, else `None`).
  - `def preview_text(body: str) -> str` (redact, collapse whitespace, first 120 characters).
- Produces, in `csa/console/inbox.py`: `InboxLastMessage(preview: str, at: datetime)`; `InboxConversation` gains `contact: InboxContact | None = None`, `last_message: InboxLastMessage | None = None`, `pending_approvals: int = 0`; `InboxConversationPage` gains `needs_review_count: int = 0`; `GET /conversations` gains `needs_review: bool = False`, `sender: Literal["known","new"] | None`, `q: str | None (max_length 64)`; a `q` that fails `parse_search` is 422 `"search needs at least two characters"`.
- Behaviour (spec 7.3, 7.5): known means an order, a subscription, or a non-blank email or address on the contact; `needs_review` means an approval with status `pending` and `expires_at > now()`; list order is `COALESCE(last message time, opened_at) DESC, id DESC` with a keyset cursor that stays the conversation id; `needs_review_count` ignores every filter.

- [ ] **Step 1: Write the pure tests** in `tests/test_inbox_contact.py` (no Docker): `test_mask_phone_keeps_last_four` (the three cases above), `test_display_name_hides_number_only_names` (`"+60125550148"` with phone `"+60125550148"` gives `None`; `"Nur Aisyah"` is kept; `"  "` gives `None`), `test_parse_search_rules` (`" @Aisyah "` gives like `%Aisyah%`; `"a"` and `" @ "` raise `ValueError`; `"50%_\\"` escapes all three; `"012 555"` gives digits `"012555"`; `"Li"` gives `digits is None`), `test_preview_text_redacts_and_truncates` (an email becomes a placeholder; a 300-character body gives at most 120 characters; newlines become spaces).
- [ ] **Step 2: Run** `bash <ci.sh> uv run pytest tests/test_inbox_contact.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `csa/console/inbox_contact.py` with the signatures above. Reuse `csa.core.pii.redact`.
- [ ] **Step 4: Run again.** Expected: 4 passed.
- [ ] **Step 5: Write the harness tests** in `tests/test_console_inbox.py`, extending `seed()` with optional contact name, phone, email, address and message times (read the existing helpers first): `test_list_returns_contact_last_message_and_pending_count` (name and phone stored; list item has `contact.phone_masked == "••••0148"` and `contact.phone is None`; the single read has `contact.phone == "+60125550148"`; a body holding an email shows redacted in `last_message.preview`; one held approval gives `pending_approvals == 1`), `test_known_versus_new_sender` (order, subscription, email and address each make `known` true; name plus phone alone is false; `sender=known` and `sender=new` return the right sets across pages), `test_needs_review_filter_and_count_ignore_expired` (an approval past `expires_at` counts for nothing; `needs_review_count` is tenant-wide and unchanged by `channel=` and `q=`), `test_list_orders_by_last_message_with_stable_cursor` (a new message on the oldest conversation moves it first; `limit=1` paging visits every conversation once; a conversation with no messages sorts by `opened_at`), `test_search_matches_name_phone_formats_and_escapes` (`aisyah`, `AISYAH`, `0125550148`, `012 555`, `+60125550148` match; `%` and `_` match only a name that literally contains them; `a` and a 65-character `q` are 422; a name `Li` is found), `test_search_never_leaks_other_tenants`, `test_new_params_keep_role_rules` (read_only 403, foreign tenant 404 unchanged), `test_unnamed_whatsapp_sender_shows_masked_number` (stored name equals `+60125550148`: `contact.display_name is None`).
- [ ] **Step 6: Run** `bash <ci.sh> uv run pytest tests/test_console_inbox.py -v`. Expected: the new tests FAIL.
- [ ] **Step 7: Implement** the SQL and routes. `list_conversations.sql` takes named parameters (the ingress SQL already does); `known` and the last-message lateral join live in SQL; `contact` and `last_message` are assembled in Python with the helpers; `get_conversation.sql` returns the contact fields and the single read fills `phone`. Keep the sandbox exclusions and the `channel IN ('telegram','whatsapp')` filter exactly as they are. Add `count_needs_review.sql`.
- [ ] **Step 8: Check logging.** Open `apps/gateway/main.py` and `csa/console/middleware.py` and the uvicorn run commands (`Makefile`, compose files) for anything that records query strings. If found, make `q` not appear in those logs; if none, state that in the commit message. Do not write `q` to the audit log.
- [ ] **Step 9: Run** `tests/test_console_inbox.py` again, then `bash <ci.sh> uv run pytest tests/test_console_contract_snapshot.py`. Expected: inbox PASS; the snapshot FAILS until step 10.
- [ ] **Step 10: Docs and snapshot.** Update the inbox table and prose in `docs/CONSOLE_API.md` (new fields, three filters, ordering, `needs_review` definition, phone masking, the search rules from spec 7.5). Run `bash <ci.sh> uv run python -m scripts.export_console_openapi`; the snapshot test now passes. Run `bash <ci.sh> make lint`.
- [ ] **Step 11: Commit** (local): `feat(inbox): contact, last message, filters, search and last-activity order`.

## Task 2 (B2): Migration 0500 and Telegram username capture

**Files:** `db/migrations/0500_contact_telegram_username.sql`, `apps/gateway/main.py`, `db/migrations/README.md`, `csa/channels/telegram.py`, `csa/channels/ingress.py`, the four ingress SQL files, `db/sql/inbox/*.sql` (search and read the username), `csa/console/inbox_contact.py`, docs; tests `tests/test_telegram.py`, `tests/test_ingress.py`, `tests/test_contact_username_schema.py`, `tests/test_console_inbox.py`.

**Interfaces:**
- Consumes: Task B1's `InboxContact.telegram_username` and the search SQL.
- Produces: column `contacts.telegram_username text CHECK (telegram_username ~ '^[A-Za-z0-9_]{4,32}$')`, nullable; `TelegramEvent.username: str | None = None`; `def telegram_username(value: object) -> str | None` in `csa/channels/telegram.py` (returns the value only if it is a string matching the pattern, else `None`).
- Behaviour (spec 7.6): every incoming Telegram message with a sender sets `telegram_username` to the normalised value, including NULL when the sender has none or an invalid one, only when it differs; this must never fail the contact update, the conversation or the message. `q` also matches the username (case-insensitive substring, leading `@` ignored).

- [ ] **Step 1: Write failing tests.** `tests/test_telegram.py::test_username_is_parsed_and_invalid_values_become_none` (parametrised: `"aisyah_r"` kept; `"ab"`, `"has space"`, `"a-b-c-d"`, `""`, `123`, `None` give `None`; the adapter puts it on `TelegramEvent.username` from `message.from.username`). `tests/test_contact_username_schema.py` (Docker): the column exists and is nullable; inserting `'aisyah_r'` works; inserting `'ab'` violates the check. `tests/test_ingress.py::test_telegram_username_is_stored_updated_and_never_blocks_a_message` (Docker; follow its existing webhook helpers): first message with `aisyah_r` stores it; a later message with `new_name_1` updates it; a message with an invalid username is accepted exactly like a normal one (same status, conversation and message rows created) and the stored username becomes NULL; a message with no username also gives NULL; the conflict path (contact created by a concurrent insert) still ends with the right username. `tests/test_console_inbox.py::test_username_is_returned_and_searchable` (`telegram_username` in list and detail; `q=aisyah_r`, `q=@AISYAH_R` and `q=isyah` find it).
- [ ] **Step 2: Run** each of the four files once (`bash <ci.sh> uv run pytest <file> -v`). Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the migration (additive, no RLS change, no data backfill), bump `EXPECTED_SCHEMA_VERSION` to `"0500"`, add the 0500 to 0509 range line to `db/migrations/README.md`, add `telegram_username()` and the event field, pass `username` through `_resolve_contact`, and make the three resolve paths set the username with one shared statement (`update_telegram_username.sql`, `UPDATE ... WHERE id = %s AND telegram_username IS DISTINCT FROM %s`). The value is always the normalised one, so the CHECK can never reject it.
- [ ] **Step 4: Run** the four files again plus `tests/test_migrate.py` and `tests/test_health.py` (they read the expected version). Expected: all PASS.
- [ ] **Step 5: Docs and snapshot.** Add the username to the `docs/CONSOLE_API.md` field list; regenerate `docs/console_openapi.json`; `make lint`.
- [ ] **Step 6: Commit** (local): `feat(inbox): capture the Telegram username on the contact (migration 0500)`.

## Task 3 (F1): Fonts, theme base and restyled shared classes

**Files:** `web/app/layout.tsx`, `web/app/globals.css`, `web/components/console/ui.tsx`.

**Interfaces:** Produces CSS variables `--font-space-grotesk`, `--font-inter`, `--font-manrope` on `<html>`; `ui.tsx` keeps the exports `field`, `action`, `secondary`, `Notice`, `Heading`, `Panel` with unchanged signatures.

- [ ] **Step 1: Read** `node_modules/next/dist/docs/01-app/03-api-reference/02-components/font.md`. Load Space Grotesk, Inter and Manrope with `next/font/google` (subsets `latin`, `display: "swap"`, CSS `variable` option) in `layout.tsx` and put the three variable classes on `<html>`. The build downloads them, which works in CI and the Docker build; if this host cannot reach Google Fonts, report it and switch to `next/font/local` with the files added to the `check-isolated` list.
- [ ] **Step 2: Wire the tokens** in `globals.css` (do not edit `fourstacks-tokens.css`): override `--fs-font-body`, `--fs-font-heading`, `--fs-font-brand` to use the three variables with the existing fallbacks; point `--font-sans` in `@theme` at Inter; keep light mode forced.
- [ ] **Step 3: Restyle `ui.tsx` class strings** to spec 7.1: 6 px control radius, 14 px panel radius, 40 px controls with a 44 px minimum touch target, Space Grotesk headings, the `--fs-focus` ring, 120 to 180 ms hover and press feedback with the `--fs-ease-out` curve. Keep every className token the existing tests rely on (search `tests/` for strings taken from `ui.tsx` first).
- [ ] **Step 4: Verify** `bash <ci.sh> npm --prefix CSA_WebConsole/csa-console/web run lint`, `... run typecheck`, `... test`. Expected: all pass.
- [ ] **Step 5: Commit** (local): `feat(console): load the FourStacks fonts and restyle the shared classes`.

## Task 4 (F2): Avatar set and `AgentAvatar`

**Files:** the 11 SVGs in `web/public/fourstacks/agents/`; the five new SVGs and manifest entries in `ui-guide/assets/agents/`; `web/lib/agent-avatars.ts`; `web/components/console/fs/AgentAvatar.tsx`; `web/scripts/check-isolated.mjs`; test `web/tests/agent-avatars.test.mjs`.

**Interfaces:**
- Produces: `export const AGENT_IDS` (the eleven IDs in spec 7.1), `export type AgentId`, `export function avatarForAgent(name: string): AgentId | null`, `export const AGENT_TITLES: Record<AgentId, string>`; `AgentAvatar({ agentId: AgentId; size?: 24|32|40|48|64|96; label?: string })` (rounded tile, creature uncropped, never a circle; empty `alt` unless `label` is given).

- [ ] **Step 1: Write the failing test** `agent-avatars.test.mjs`: `avatarForAgent("kb_agent") === "knowledge"`, `"ticket_agent"` and `"escalation_agent"` give `"tickets"`, `"order_agent"` and `"order_approval"` give `"orders"`, `"invoice_agent"` gives `"invoices"`, `"ecommerce_agent"` gives `"ecommerce"`, `"subscription_agent"` gives `"subscriptions"`, `"booking_agent"` gives `"bookings"`, `"lhdn_agent"` and `"nonsense"` give `null`, and each of `support`, `assistant`, `hr`, `email` maps to itself; `AGENT_IDS.length === 11` with no duplicates. (Load the module as `channels-transport.test.mjs` does, by transpiling the source.)
- [ ] **Step 2: Run** `bash <ci.sh> npm --prefix CSA_WebConsole/csa-console/web test`. Expected: FAIL.
- [ ] **Step 3: Implement** `agent-avatars.ts`. Copy the six SVGs from `ui-guide/assets/agents/` and the five new ones (`assistant`, `hr`, `email`, `subscriptions`, `bookings`) into `web/public/fourstacks/agents/`. The five new SVGs come from the session scratchpad generator output at `/tmp/claude-1000/-home-amsyrzmi-CSAV2/44845bab-014a-4610-95de-184b6a6d4038/scratchpad/avatars/out/`; copy them and the generator (`gen.mjs`) to `ui-guide/assets/agents/` and `ui-guide/scripts/draft-new-avatars.mjs`, and add five entries (`working: null`) to `manifest.json`. Note in the commit that `export-avatars.mjs` regenerates the manifest from the engine and will drop them until the profiles join the engine in phase 2.
- [ ] **Step 4: Implement `AgentAvatar.tsx`** following `ui-guide/examples/AgentAvatar.tsx`, using the pale rounded tile from the approved mockup (`linear-gradient(160deg,#EDF2FF,#DCE9FF)`, radius 10 px). Register in `check-isolated.mjs`: `lib/agent-avatars.ts`, the test, and each of the 11 SVG paths (the walker only allows `.ts`, `.tsx`, `.css`, `.ico` under `app` and `components/console`).
- [ ] **Step 5: Run** `npm test`, `lint`, `typecheck`. Expected: PASS.
- [ ] **Step 6: Commit** (local): `feat(console): the eleven agent avatars and AgentAvatar`.

## Task 5 (F3): Customer identity helpers and `CustomerAvatar`

**Files:** `web/lib/customer-identity.ts`, `web/components/console/fs/CustomerAvatar.tsx`, `web/tests/customer-identity.test.mjs`, `check-isolated.mjs`.

**Interfaces:** Produces `initialsFor(name: string | null): string | null` (first code point of each of the first two words, upper-cased; `"Nur Aisyah Rahman"` gives `"NA"`, `"Tan Wei Jian"` gives `"TW"`, `"Priya"` gives `"P"`, blank gives `null`), `tintIndex(id: string): 0 | 1 | 2 | 3` (stable FNV-1a over the id), `rowTitle(c: {display_name, telegram_username, phone_masked}): string` (name, else `@username`, else masked number, else `"Unknown sender"`), `secondaryLine(c, channel: "telegram" | "whatsapp"): string` (`"@aisyah_r · Telegram"`, `"••••0148 · WhatsApp"`), `senderLabel(known: boolean): "Customer" | "New sender"`; `CustomerAvatar({ id, name, size })` (round, blue tint 0 to 3 from the approved palette `#D9E5FF/#183DA5`, `#CFEBFF/#0B5A8C`, `#E0E6FB/#2C3E9E`, `#D2EEF6/#164B8D`, text contrast at least 4.5:1; dashed circle with `?` when `initialsFor` is `null`).

- [ ] **Step 1: Write the failing tests** (cases above, plus: `tintIndex` returns the same value on repeat calls and spreads 200 generated UUIDs so each tint gets at least 10; `rowTitle` for a contact with only a masked number returns `"••••0148"`; with nothing returns `"Unknown sender"`).
- [ ] **Step 2: Run** `npm test`. Expected: FAIL. **Step 3: Implement.** **Step 4: Run.** Expected: PASS. Check the four tint pairs for 4.5:1 contrast and adjust within the blue family if any fails. Register the files in `check-isolated.mjs`.
- [ ] **Step 5: Commit** (local): `feat(console): customer identity helpers and CustomerAvatar`.

## Task 6 (F4): Motion rules

**Files:** `web/lib/motion.ts`, `web/components/console/fs/useMotionAllowed.ts`, `web/app/globals.css` (keyframes), `web/tests/motion.test.mjs`, `check-isolated.mjs`.

**Interfaces:** Produces `interface MotionInputs { reducedMotion: boolean; documentHidden: boolean; offscreen: boolean; paused: boolean }`, `motionAllowed(i: MotionInputs): boolean` (true only when all four are false), `PAUSE_KEY = "fs-motion-paused"`, `parsePause(raw: string | null): boolean` (`"1"` only), and `useMotionAllowed(ref?: React.RefObject<Element | null>): boolean` (reads `matchMedia("(prefers-reduced-motion: reduce)")`, `document.visibilityState`, an `IntersectionObserver` on `ref` when given, and `localStorage[PAUSE_KEY]`, and updates on their change events). CSS: `fs-message-in`, `fs-nudge`, `fs-slide-up`, `fs-fade` keyframes at 120 to 240 ms with `--fs-ease-out`, each wrapped so they do nothing under `prefers-reduced-motion: reduce`.

- [ ] **Steps 1 to 4:** test `motionAllowed` over all 16 input combinations and `parsePause` for `"1"`, `"0"`, `null`, `"true"` (only `"1"` pauses); run and see it fail; implement; run and see it pass; `lint` and `typecheck`; register files; commit (local): `feat(console): shared motion rules and pause preference`.

## Task 7 (F5): Primitives: status label, skeleton, slide-over, list states, shortcuts

**Files:** `web/components/console/fs/{StatusLabel,Skeleton,SlideOver,ListStates}.tsx`, `web/lib/inbox-shortcuts.ts`, `web/tests/inbox-shortcuts.test.mjs`, `check-isolated.mjs`.

**Interfaces:**
- `StatusLabel({ tone: "neutral"|"info"|"attention"|"success"|"error"|"customer"|"team"; children })` (always text; colours per the approved mockup).
- `Skeleton({ className })` and `RowSkeleton` (shaped like a list row; shimmer stops under reduced motion).
- `SlideOver({ open, onClose, title, returnFocusTo?, children })` on native `<dialog>`: `showModal()` on open, focus moves in, Escape and the Close button call `onClose` and focus returns to the opener, background does not scroll.
- `ListStates`: `type ListStateKind = "loading"|"empty"|"unavailable"|"partial"|"forbidden"`; `ListState({ kind, onRetry?, onLoadOlder?, connectHref?: string | null })`, rendering the copy in spec 7.7 (no retry on `forbidden`; the Connect WhatsApp button only when `connectHref` is a string); each root has `data-testid="inbox-list-state-<kind>"`.
- `export type Shortcut = "next" | "previous" | "close"`; `export function shortcutFor(event: KeyLike, ctx: { dialogOpen: boolean }): Shortcut | null` where `KeyLike = { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean; target: { tagName?: string; isContentEditable?: boolean } | null }`.

- [ ] **Step 1: Write the failing test** `inbox-shortcuts.test.mjs`: `j` and `k` give `next` and `previous` on a plain target; `J` too; ignored when `target.tagName` is `INPUT`, `TEXTAREA`, `SELECT`, or `isContentEditable` is true; ignored with `ctrlKey`, `metaKey` or `altKey`; ignored when `dialogOpen`; `Escape` gives `close` when not `dialogOpen` and `null` when it is (the dialog closes itself); any other key gives `null`.
- [ ] **Step 2: Run** `npm test`; FAIL. **Step 3: Implement** `shortcutFor` and the five components. **Step 4: Run** `npm test`, `lint`, `typecheck`; PASS. Register the files.
- [ ] **Step 5: Commit** (local): `feat(console): status labels, skeletons, slide-over, list states and shortcuts`.

## Task 8 (F6): Inbox API types, query builder and data hook

**Files:** `web/lib/inbox-api.ts`, `web/components/console/inbox/usePages.ts`, `web/tests/inbox-api.test.mjs`, `check-isolated.mjs`.

**Interfaces:** `lib/inbox-api.ts` exports the types mirroring Tasks B1 and B2 (`Conversation`, `Contact`, `LastMessage`, `Message`, `Approval`, `Page<T>`, with `needs_review_count` on the conversation page), `inboxListQuery(o: { tenantQuery: string; channel: ChannelFilter; needsReview: boolean; sender: "" | "known" | "new"; q: string }): string` (builds `…/conversations?` with only the set parameters, trims `q`, drops a `q` shorter than two characters, URL-encodes), and `canConnectWhatsApp(role: string | null): boolean` (true for `platform_admin` and `tenant_admin`). `usePages<T>(path, pages, revision)` moves out of `InboxWorkspace.tsx` unchanged in behaviour (3-second refresh, 15-second timeout) and additionally returns `loaded: boolean` and keeps the previous `items` when a refresh fails, setting `error`.

- [ ] **Step 1: Write the failing tests:** query builder (set and unset combinations, `q` with `%`, spaces, `@` and unicode encoded correctly, one-character `q` dropped); `canConnectWhatsApp` for the four roles and `null`.
- [ ] **Step 2: Run, implement, run.** Move the types out of `InboxWorkspace.tsx`. `lint`, `typecheck`, `npm test` PASS. Register the files.
- [ ] **Step 3: Commit** (local): `feat(console): inbox API types, query builder and data hook`.

## Task 9 (F7): The inbox UI

**Files:** `web/components/console/InboxWorkspace.tsx` and `web/components/console/inbox/{Inbox,ConversationList,Thread,ReviewCard,CustomerCard}.tsx`.

**Interfaces:** Consumes everything above. Keeps `export function InboxWorkspace({ me }: { me: Me })` and the tenant `<label>Tenant</label>` select with the text `Select a tenant to open its inbox.`. Keeps the existing submit/propose/notice flows, the proposal card, `data-testid="inbox-channel-filter"` (the `<select>`), `inbox-channel-badge` (inside each list row's button, so clicking it selects the conversation), `inbox-delivery-notice`, and the who-line text `Staff · not delivered` (author label, ` · `, `deliveryLabel`). New test ids: `inbox-search`, `inbox-filter-all`, `inbox-filter-needs-review`, `inbox-needs-review-count`, `inbox-filter-customers`, `inbox-filter-new-senders`, `inbox-thread`, `inbox-thread-channel`, `inbox-details-button`, `inbox-customer-card`, `inbox-new-messages-pill`.

- [ ] **Step 1: Read first:** the current `InboxWorkspace.tsx`, `ProposalCard.tsx`, `lib/channels-api.ts`, the approved mockups (`inbox-blue-v2.html`, `inbox-responsive-states.html`, `new-avatars-v1.html` in the session's brainstorm folder) and spec 7.2 to 7.7.
- [ ] **Step 2: Build the layout.** Three panes at 1100 px and above; at 1024 px and below the card is the `SlideOver` behind `inbox-details-button`; on a phone the list and thread are separate full-width views with a back button. The list has the search box (debounced, server-side via `inboxListQuery`), the four chips with the always-visible count (from `needs_review_count`), the channel select, rows (`CustomerAvatar`, `rowTitle`, `secondaryLine`, preview, relative time, `StatusLabel`s for `senderLabel`, owner and Needs review) that ellipsise long names and never wrap a 60-character unbroken name past the row, and `ListState` for the five states. A failed refresh keeps rows and shows `unavailable`; a `403` shows `forbidden`.
- [ ] **Step 3: Build the thread:** day dividers, grouped bubbles, customer left; assistant messages with `AgentAvatar agentId="support"` and a blue-edged bubble; staff messages as a dark "Team" avatar in deep blue (no personal initials, spec 9); delivery text and `inbox-delivery-notice` as today; the held reply as an inline `ReviewCard` (an `<article>` with the buttons `Approve`, `Reject`, `Edit`, `Queue edited reply`, the textarea, and `View conversation` where a card is shown outside its thread) going through the existing proposal flow; an expired-but-pending approval is read-only and labelled expired; `Take over`, `Take over indefinitely` and `Release to AI` keep today's confirmations and wording.
- [ ] **Step 4: Customer card:** contact details (full phone), conversation owner, that conversation's approvals from `/inbox/approvals?conversation_id=…`, with the checkbox labelled exactly `Include handled approvals`.
- [ ] **Step 5: Engagement (spec 7.4):** the six features, each gated by `useMotionAllowed`: bubble arrival with the `inbox-new-messages-pill` when scrolled up, one nudge on the count rising, `shortcutFor` on `keydown` (listener ignores events while the `SlideOver` is open), takeover room change only after the server confirms, delivery-state fade, list preview and time.
- [ ] **Step 6: Copy:** apply `docs/WRITING_GUIDE.md` to the page description, the top notice, button labels and empty states (spec 7.7); keep `Approval expired.` and the other result strings `tests/` match on. Leave the navigation label `Customer inbox` unchanged (other tests and the assistant context use it); the B3 rename is a later wave.
- [ ] **Step 7: Verify:** `npm test`, `lint`, `typecheck`. Then run the web app against synthetic data using the harness in `tests/test_whatsapp_browser.py` (Task F8) and look at 320, 390, 768, 1024 and 1440 px.
- [ ] **Step 8: Commit** (local): `feat(console): the three-pane customer inbox`.

## Task 10 (F8): Browser tests

**Files:** `tests/test_inbox_redesign_browser.py` (new), `tests/test_whatsapp_browser.py`, `tests/test_packaged_console.py`.

- [ ] **Step 1: Update stubs and flows.** In `tests/test_whatsapp_browser.py` give the `conversation()` stub the new `contact`, `last_message`, `pending_approvals` fields and the list `needs_review_count`; keep its badge, filter and `Staff · not delivered` assertions. In `tests/test_packaged_console.py` replace `No customer conversations found.` with the new empty-state text, and move the approval flow (cards found by `article` text, buttons `Approve`, `Edit`, `Reject`, `View conversation`, `Queue edited reply`, and the `Include handled approvals` checkbox) to the new places (thread review card and customer card); the packaged tests are slow and run in CI. Explain each change in the PR text.
- [ ] **Step 2: Write `tests/test_inbox_redesign_browser.py`** on the same stub-upstream harness, with synthetic rows (a named Telegram customer, a WhatsApp sender whose name is the number, a new sender, a 60-character unbroken name). Tests: `test_no_page_overflow_at_all_widths` (320, 390, 768, 1024, 1440: `scrollWidth <= innerWidth`); `test_long_name_does_not_break_the_row`; `test_needs_review_count_stays_visible_and_filters_on_the_server` (the request carries `needs_review=true`); `test_search_is_sent_to_the_server_and_ignores_one_character`; `test_failed_refresh_keeps_rows` (stub returns 500 after the first load: rows remain, `inbox-list-state-unavailable` shows, `inbox-list-state-empty` does not); `test_unnamed_whatsapp_sender_shows_masked_number_in_list_and_full_in_card`; `test_j_k_move_selection_but_not_inside_inputs` (typing `jk` in `inbox-search` and the human-reply textarea leaves the selection unchanged); `test_slide_over_opens_closes_with_escape_and_returns_focus` (at 390 px); `test_empty_state_connect_button_only_for_admins` (tenant admin sees the button, tenant staff sees the ask-your-administrator sentence and no button); `test_reduced_motion_removes_animations`.
- [ ] **Step 3: Run** `bash <ci.sh> uv run pytest tests/test_inbox_redesign_browser.py -v` and `tests/test_whatsapp_browser.py -v` if Chromium runs on this host; otherwise leave both to CI and say so.
- [ ] **Step 4: Commit** (local): `test(console): browser checks for the redesigned inbox`.

## Task 11 (F9): Close-out

- [ ] **Step 1:** Add one entry to the top of `docs/PROGRESS.md` for this phase (what shipped, the migration, the test commands run with their results, what is left for CI).
- [ ] **Step 2:** Final regeneration of `docs/console_openapi.json` if backend code changed since Task B2, then `make lint`, `npm --prefix CSA_WebConsole/csa-console/web run check:isolated` (runs the build; this host may be slow, so run it once), and the focused tests of every file changed (one per run).
- [ ] **Step 3:** Whole-branch review with the strongest model, fix, re-review; then report to the owner. Do not push or open the PR until the owner asks.
- [ ] **Step 4: Commit** (local): `docs: progress note for console redesign phase 1`.

## Self-review

- **Spec coverage:** 7.1 to F1 to F5; 7.2 to F7; 7.3 to B1 and F3; 7.4 to F4, F5, F7; 7.5 to B1; 7.6 to B2; 7.7 to F5, F7; 7.8 to B1, B2, F6, F8, F9; section 8 to F2; section 9 calls to B1 (order, needs review, unnamed names), F7 (staff avatar, support avatar), F5 (no search left out, now built). Gaps: the B3 navigation-label rename and the remaining finished tabs are deliberately later phases (noted in F7).
- **Types:** `InboxContact`, `Conversation`/`Contact` mirror each other (`display_name`, `telegram_username`, `phone_masked`, `phone`, `kind`, `known`); `needs_review_count`, `pending_approvals`, `last_message.preview/at` are named the same in B1, F6 and F7; `shortcutFor` and `Shortcut` are defined once (F5) and used in F7.
- **Proportion:** the plan is longer than the spec because it spans three layers; code bodies are left to the implementers.
