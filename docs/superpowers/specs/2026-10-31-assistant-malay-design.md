# Workspace assistant in Malay — design (S3)

Date: 2026-10-31. Branch `fix/assistant-malay`, base main `53bbe22`. Follow-up S3 of the
assistant audit (`docs/superpowers/specs/2026-10-31-assistant-audit-fixes-design.md`).

## Intent

Staff who write to the workspace assistant in Malay should get Malay back from the whole
assistant panel, not a mix of Malay and English. Today the server answers some fixed lines in
Malay, but the language guess is weak, and cards, statuses, error lines and tab names in the
panel are English only.

## Owner decisions (2026-10-31)

- **Scope:** the assistant panel only. If a staff member writes in Malay, the panel carries on
  in Malay. The rest of the console (sidebar, tabs, pages, settings) stays English.
- **The language carries on:** a conversation keeps its last clear language until the staff
  member clearly writes in the other one. A typed address, a name, "ok" or an option click
  does not switch it back to English.
- **Approach A:** the server decides each turn's language and saves it with the turn. It writes
  its own text (answers, questions, card titles and labels) in that language, and returns the
  language with every turn. The panel picks its own lines from a small English/Malay table by
  that language.

## Approaches considered

- **A (chosen):** server decides, panel follows. One decision per turn, saved with the turn, no
  migration. Cost: two phrase tables (server and console) to keep in step.
- **B: the server writes every line, statuses and errors included.** Rejected. Card statuses
  change after the card is made, so stored cards would need rewriting on every read. The panel
  still needs its own Malay lines for when the server can't be reached.
- **C: the panel guesses the language from the message.** Rejected. Two separate guesses can
  disagree, giving a Malay card with an English status under it.

## Goals

1. **Better language guess.** These all come out Malay:
   - "hantar emel kat danial@fourstacks.my pasal mesyuarat esok"
   - "emel aina.rahman.binti.ali@company.example.com.my tentang cuti"
   - "buka tab HR"
   - "tolong hantar emel kepada nur.aina.binti.ahmad@syarikat-contoh.com.my"

   These stay English: "send email to danial", "can you open HR".
2. **The language carries on.** The rule is in "Language rule" below.
3. **Server text in the turn's language:**
   - every fixed answer;
   - the locked-tab answers in both languages, which today show the raw tab key ("hr"). They
     name the tab as the console's sidebar shows it ("People & HR") and the package by its title
     on the Services page;
   - the "Not included … (no work email)" card field;
   - the titles, summaries and field labels of the cards the assistant makes.
4. **Panel text in the turn's language:**
   - card status lines (`resultSummary` and the status lines in `lib/assistant-view.ts`);
   - the Confirm/Send/Cancel buttons and their busy forms;
   - error lines for failed turns and failed card actions (the `lib/api.ts` sentences and the
     email sentences in `lib/email-recipients.ts`, as used by the dock);
   - "Opened X." / "… and continued there.", "No matching colleague was found.";
   - "Choose a colleague:", "Loading your conversation…", the "On <tab>" context line;
   - the email preview labels inside an assistant card (To, CC, BCC, Subject, "Outside your
     company");
   - the words around the tab names the panel shows (the names themselves stay as on screen,
     below).

**On-screen names stay as the console shows them (owner, 2026-10-31).** Tabs, packages,
switches and pages keep their console names inside Malay sentences, for example "tab Email
assistant", "halaman Services" and "“Approves training, leave and attendance for their team”".
The sidebar stays English, so a translated name could not be found on screen. The existing
Malay lines already do this.

## Non-goals

- The rest of the console: no language setting and no translation of tabs or pages.
- The HR assistant chat (`HrAssistant.tsx`) and cards made from a tab (origin `tab`). They stay
  English.
- The email draft itself, which keeps following the message through the drafting model as
  today.
- Model-written text, which already follows `facts.language` through the planner prompt; it
  now gets the carried-on language.
- Any language other than English and Malay.

## Anti-goals

- A wrong guess that flips a Malay conversation into English halfway through (the whole point
  of the carry-on rule).
- Malay that reads machine-made or mixes registers. Use standard Malaysian Malay with "anda",
  matching the existing Malay lines in `_TEXT`.
- Any change to what the assistant does: recipients, the send rule, gates, confirmations and
  limits are untouched. Only wording changes.
- New personal data in stored cards or turns. The counts-only email cards stay counts-only:
  no address, subject or body. The `0485` rollout check (`deploy/rollout_checks/0485.sql`)
  must still print 0.

## Language rule

- **Three outcomes.** `detect_language(text)` returns `"en"`, `"ms"` or `None` (not clear).
- **Before counting**, drop email addresses, URLs, digits, the tab and product words both
  languages share ("HR", "Gmail", "email" as a bare noun is the builder's call), and words of
  fewer than 2 letters.
- **Two word lists.**
  - Malay markers: the existing list plus everyday words such as *kat*, *pasal*, *esok*,
    *bos*, *tentang*, *mesyuarat*, *cuti*, *nanti*, *tolong*, *buat*, *hari*, *minggu*,
    *bulan*, *semua*, *berapa*, *banyak*, *baca*, *tab*-phrases like *buka*.
  - English markers: function words such as *to*, *the*, *a*, *my*, *me*, *please*, *can*,
    *you*, *for*, *about*, *open*, *send*, *what*, *how*, *show*, *need*.
- **Deciding.** A message is clear when one side has at least one marker and more markers than
  the other side. Otherwise it is `None`. A name alone ("Danial Hakim"), an address alone, "ok"
  and an option value are all `None`.
- **Thresholds.** The exact ones are the builder's call, pinned by the examples in Goals 1
  plus these:

  | Message | Result |
  |---|---|
  | "ok" | `None` |
  | "danial@fourstacks.my" | `None` |
  | "Danial Hakim" | `None` |
  | "ya" | `ms` (a one-word Malay reply to a question is clear) |
  | "yes" | `en` |

- **What is classified:** only what the staff member typed. For a clarification answer or a
  colleague pick, that is the typed part that `_authored` / `_answer_parts` already split out,
  not the folded "(Answer to … for: …)" question, the origin or "Use employee … for:". A click
  with nothing typed is `None`.
- **The turn's language** is the message's clear language. When it is `None`, it is the
  previous turn's saved language in the same thread. With no previous turn it is `"en"`.
- **Where it applies:** the planner facts, every server line and the saved turn all use this
  one value. Turns that don't go through the planner (the guidance and order-read path, and
  denied or unavailable results) get the same rule and save the carried language too.

## How the language travels

- **Saved with the turn.** The language is stored in the existing `result` jsonb of
  `console_workspace_turns` (key `language`), so no migration is needed.
  - The stored result is a strict union (`extra="forbid"`), parsed by `_stored_result` in
    `list_turns`, `_prior_turns`, `_replayed`, `_known_turn` and the existing-key path of
    `create_turn`. One helper must split `language` off before every parse.
  - `_insert_turn` writes it next to the dumped result and takes the language as its own
    argument, because the result it returns is the unstored one.
  - On read it is lifted out to a new field `WorkspaceTurnOut.language: "en" | "ms"`.
  - Rows without it (from before this change) read as `"en"`.
  - The previous turn's language for the carry-on rule comes from the same stored key, read
    with the history query (`db/sql/workspace/turn_history.sql` already returns `result`).
- **Cards are written in the turn's language when they are made, and carry it.**
  - **Plumbing:** `card()` gains a `language` argument (default `"en"`) through the
    `ProposalExecutor` protocol, `ProposalRegistry.prepare` and every executor, including
    `SubscriptionActionExecutor` in `csa/subscriptions`.
  - **The confirm-time re-check** (`registry.py` around line 310) throws the content away, so
    it can pass the default.
  - **Not in the payload:** the language never goes into the payload or its sha256.
  - **On the card:** `ProposalCard` (the contract, stored in the `card` jsonb) gets
    `language: "en" | "ms" = "en"`. Old stored cards read as English.
  - **Rendering:** the console's `ProposalCard` component renders its buttons, status lines,
    "Expires …" line, email preview and error lines from `card.language` wherever it is shown.
    A Malay card in another tab is then fully Malay, never Malay with English chrome. No prop
    plumbing is needed through the other tabs.
  - **The two hidden sentinel fields** on the email cards stay English: label `"Subject"`
    (value `"(shown on open)"`) and label `"Draft"`. `draftIdFromCard` and `cardFieldsShown`
    find them by those labels, the panel never shows them, and the `0485` check keys on them.
    Every visible title, summary and label is translated.
- **The panel** renders each turn's own lines ("Opened X.", "No matching colleague…", "Choose a
  colleague:") in that turn's `language`.
  - **Panel-wide lines** ("On <tab>", notices) use the latest turn's language.
  - **Before any turn exists** ("Loading your conversation…", an empty new conversation) the
    panel is English.
  - **A failed turn** (no stored turn comes back) uses the latest turn's language, else English.
    So a first message that fails gets an English error. That limitation is acceptable and
    noted.
- **Error lines** are looked up by `ApiError.code` in a code-keyed en/ms table, not taken from
  the English `message` that `decode()` builds.
  - Only the codes an assistant turn or a card Confirm/Cancel can return get Malay. The
    subscriptions, invitations and WhatsApp codes the dock never sees stay English-only.
  - An unmapped code or "Request failed (N)" in Malay gets one Malay fallback line: "Sesuatu
    tidak kena. Cuba lagi." (or similar).
- **`EmailPreview` and the email error helpers** take a `language` argument defaulting to `"en"`.
  The Email tab and every other non-card caller stay English with no change.

## Constraints

- Python 3.12, FastAPI, pydantic v2. Console in `CSA_WebConsole/csa-console/web` (Next.js,
  TypeScript, node test runner in `tests/*.test.mjs`).
- No migration, no new dependency, no new role. RLS untouched.
- `csa/workspace/planner.py` is shared: change only `detect_language`, its word lists and what
  it returns.
- Locally, run each changed test file on its own, plus `make lint`, web `npm test`, lint and
  typecheck. The full suite runs in PR CI.
- **Overlap with the console redesign (another session).** Its branch
  `feat/console-redesign-phase-2` changes `tabLabel`/`tabHref` in `lib/assistant-view.ts`: the
  sandbox chat (`chat`) opens and is named as Overview. The panel keeps using `tabLabel` for
  tab names, so it follows that rename by itself. Don't touch `navigation.ts`.
- **Smoke run and checklist.**
  - Add smoke cases for the new detection examples where they reach the planner (Malay
    messages with an address).
  - Update the Malay rows of `docs/ASSISTANT_RETEST.md`: drop "console error sentences are
    English only", and add a carry-on row (a Malay request, then a typed address only, which
    stays Malay).

## Left to the builder

- **Malay wording** of every line, in the register above, with on-screen names kept as the
  console shows them.
- **The server's tab names** for the locked-tab answer are a copy of the sidebar labels in
  `lib/navigation.ts` (for example `hr` is "People & HR"). Keep that copy next to `_TAB_GATES`,
  with a test that checks it against `navigation.ts`.
- **Where the tables live.** For example, a `ms` branch next to `_TEXT` on the server and a
  `lib/assistant-copy.ts` (en/ms) in the console. Keep every key present in both languages,
  with a test that checks that.
- **The exact word lists and thresholds,** within the rule and examples above.
