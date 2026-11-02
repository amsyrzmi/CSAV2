# Workspace Assistant in Malay — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a staff member who writes to the workspace assistant in Malay gets the whole assistant
panel in Malay, and the conversation keeps that language until they clearly write English.

**Architecture:**
- **Server:** each turn's language is decided once (a three-way guess on the user's own words,
  carried from the previous turn when unclear). It is saved in the turn's stored `result` jsonb
  and returned as `WorkspaceTurnOut.language`.
- **Cards:** written in that language when they are made, and carry it as
  `ProposalCard.language`.
- **Console:** the panel picks its own lines from an en/ms table by the turn's or card's
  language.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, psycopg3, Postgres 16 (testcontainers);
Next.js console with TypeScript, node test runner (`tests/*.test.mjs`).

**Spec:** `docs/superpowers/specs/2026-10-31-assistant-malay-design.md`

## Global Constraints

- **No schema change:** no migration, no new dependency, no new role; RLS untouched.
- **Behaviour:** no change to what the assistant does (recipients, send rule, gates,
  confirmations, limits). Only wording changes.
- **On-screen names** (tabs, packages, switches, pages) stay exactly as the console shows them,
  inside Malay sentences: "tab Email assistant", "halaman Services".
- **Malay register:** standard Malaysian Malay with "anda", matching the existing `_TEXT["ms"]`.
- **Counts-only email cards:**
  - no address, subject or body in a card or stored turn;
  - the hidden sentinel fields stay exactly `CardField(label="Subject", value="(shown on open)")`
    and `CardField(label="Draft", value=<draft id>)` in both languages;
  - `deploy/rollout_checks/0485.sql` must still print 0.
- **Protocol strings the server parses stay English** in both languages: the console's
  "(Answer to “…” for: …)" fold and "Use employee <id> for: <origin>".
- **`Language = Literal["en", "ms"]`** is defined once in `csa/workspace/contracts.py` and
  imported everywhere else.
- **Defaults:** missing language reads as `"en"` everywhere (old turns, old cards, tab-origin
  cards, non-dock callers).
- **`csa/workspace/planner.py` (shared):** only `detect_language`, its word lists and its return
  type change.
- **Don't touch `CSA_WebConsole/csa-console/web/lib/navigation.ts`** (the redesign session owns
  it).
- **Tests:** each changed Python test file run on its own with the ci wrapper; `make lint`; web
  `npm test`, `npm run lint`, `npm run typecheck`. The full suite runs in PR CI.

## Review Focus

1. **A Malay conversation, then a typed address only:** the turn and the Send card that follow
   stay Malay (Task 2's carry test, and Task 4's card test on such a turn).
2. **A colleague pick ("Use employee <id> for: <Malay origin>") or an option click** never
   counts as English (Task 2, `own_words` tests).
3. **A turn stored before this change** (no `language` key) still lists, replays and feeds
   history without a validation error (Task 2, an old-row test).
4. **A Malay email card:** `draftIdFromCard`/`cardFieldsShown` still find and hide the sentinel
   fields, and `0485.sql` prints 0 against it (Task 4 server; Task 5 console).
5. **A failed turn or failed Confirm in a Malay conversation** shows a Malay line, including an
   unmapped code and a non-JSON 5xx (Task 5).

---

### Task 1: Three-way language guess

**Files:**
- Modify: `csa/workspace/contracts.py` (add `Language`)
- Modify: `csa/workspace/planner.py:169-185` (`_MALAY_MARKERS`, new `_ENGLISH_MARKERS`,
  `detect_language`; `PlannerFacts.language: Language`)
- Test: `tests/test_workspace_planner.py` (pure tests near `test_detect_language_recognises_malay`)

**Interfaces:**
- Produces: `Language = Literal["en", "ms"]` in `csa.workspace.contracts`.
- Produces: `detect_language(text: str) -> Language | None`. `None` means not clear.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hantar emel kat danial@fourstacks.my pasal mesyuarat esok", "ms"),
        ("emel aina.rahman.binti.ali@company.example.com.my tentang cuti", "ms"),
        ("buka tab HR", "ms"),
        ("tolong hantar emel kepada nur.aina.binti.ahmad@syarikat-contoh.com.my", "ms"),
        ("saya nak mohon latihan CompTIA A+", "ms"),
        ("ya", "ms"),
        ("send email to danial", "en"),
        ("can you open HR", "en"),
        ("yes", "en"),
        ("ok", None),
        ("danial@fourstacks.my", None),
        ("Danial Hakim", None),
        ("", None),
    ],
)
def test_detect_language_three_way(text: str, expected: str | None) -> None:
    assert detect_language(text) == expected
```

Update `test_detect_language_recognises_malay` only where it asserted `"en"` for text that is
now `None`; note each change in the commit message.

- [ ] **Step 2: Run it and check it fails**

Run: `bash <ci.sh> .venv/bin/python -m pytest tests/test_workspace_planner.py -q -k detect_language`
Expected: FAIL (for example "buka tab HR" returns `"en"`).

- [ ] **Step 3: Implement `detect_language`**

The rule (spec, "Language rule"):
- **Strip first:** remove email addresses and URLs (regex) before splitting into letter words.
- **Drop:** words under 2 letters, and the shared words `hr`, `gmail`, `tab`, `ok`, `okay`.
- **Count:** Malay markers (the existing list plus the spec's everyday words, and `ya`) and
  English markers (the spec's function words, plus `yes`, `email`, `emails`).
- **Decide:** return the side with at least one marker and strictly more markers, else `None`.

`buka tab HR` → `buka` is Malay and `tab`/`HR` are dropped, so `ms`. Switch
`PlannerFacts.language` to `Language`.

- [ ] **Step 4: Run it and check it passes**

Same command. Expected: PASS. `planner_facts` still passes the result straight into
`PlannerFacts`, so wrap that call as `detect_language(turn.body) or "en"` until Task 2 replaces
it. Run the whole file once to confirm nothing else broke.

- [ ] **Step 5: Commit**

`git commit -m "feat(workspace): three-way language guess that ignores addresses and shared words"`

---

### Task 2: The turn's language: decided, carried, saved and returned

**Files:**
- Modify: `csa/workspace/contracts.py` (`WorkspaceTurnOut.language: Language = "en"`)
- Modify: `csa/workspace/service.py`:
  - `_stored_result` at line 135 and every caller: `create_turn` existing-key path,
    `list_turns`, `_prior_turns`, `_known_turn`;
  - `_replayed` (319);
  - `_insert_turn` (381);
  - `planner_facts` (866–911);
  - `create_planned_turn`;
  - `create_turn` (332).
- Modify: `db/sql/workspace/turn_last.sql` (also select `result`)
- Regenerate: `python -m scripts.export_console_openapi` (contract snapshot)
- Test: `tests/test_workspace_planner.py`, `tests/test_console_workspace.py`

**Interfaces:**
- Consumes: `detect_language` and `Language` (Task 1).
- Produces:
  - `_split_stored(value: Any) -> tuple[WorkspaceActionResult, Language]`: pops `language`
    (default `"en"`), then validates. It replaces `_stored_result` at every read site.
  - `own_words(body: str, offered: list[str]) -> str`: the typed part from `_answer_parts`.
    It is `""` when that typed part is one of `offered` (`_picked`), and `""` when the whole
    body is a colleague pick `^Use employee <uuid> for: `.
  - `turn_language(body: str, offered: list[str], previous: Language | None) -> Language`: it is
    `detect_language(own_words(body, offered)) or previous or "en"`.
  - `_insert_turn(..., language: Language)`, a new required keyword. It stores
    `{**dumped, "language": language}` and returns a `WorkspaceTurnOut` with `language`.
  - `_replayed(row, result, language)`.
  - `PlannerFacts.language` is now `turn_language(...)`, with `previous` read from the newest
    history row and `offered` taken from the newest history turn's `offered_options`.

- [ ] **Step 1: Write the failing tests**

Pure tests:
- `test_own_words_drops_folds_picks_and_colleague_picks`, which asserts:
  - `own_words("Use employee 1b9d…-uuid for: hantar emel kepada Danial", []) == ""`;
  - for a folded answer, `own_words` returns only the typed part;
  - `own_words("danial@x.my", ["danial@x.my"]) == ""`.
- `test_turn_language_carries`, which asserts:
  - `turn_language("danial@fourstacks.my", [], "ms") == "ms"`;
  - `turn_language("ok", [], None) == "en"`;
  - `turn_language("send it to danial please", [], "ms") == "en"`.

Docker tests (`planned` fixture, `_mail_ready`, `_thread`, `_turn`, `model.reply`):
- `test_malay_conversation_stays_malay_after_a_typed_address`:
  1. a Malay turn (`"tolong hantar emel kepada Danial"`), answered with
     `{"action": "clarification", "question": "...", "options": []}`;
  2. then a turn whose body is just `"danial@fourstacks.my"` (planner replies with an answer);
  3. assert both responses have `json()["language"] == "ms"`, and that the second planner
     call's facts carry `language == "ms"` (read through the scripted model's recorded
     prompts);
  4. assert `GET` turns returns `"ms"` for both.
- `test_turn_stored_without_language_reads_as_english`:
  1. insert a turn row directly with an old-shape `result` (no `language` key);
  2. list turns, replay its request key, and run a following turn;
  3. assert no error and `language == "en"`.

In `tests/test_console_workspace.py`, add `test_legacy_turn_carries_thread_language`: a legacy
(no `page_context`) turn after a stored Malay turn returns `"ms"`.

- [ ] **Step 2: Run them and check they fail**

Run each file on its own with the ci wrapper. Expected: FAIL (`language` missing, `own_words`
undefined).

- [ ] **Step 3: Implement the interfaces above**

- **Legacy path:** `create_turn` reads the previous language through `turn_last.sql` (now
  `SELECT page_key, result`), with `offered=[]`.
- **Planner path:** `create_planned_turn` passes `facts.language` to `_insert_turn`.
- **Replays:** a replay returns the stored language.

- [ ] **Step 4: Regenerate the contract, run and check they pass**

Run `.venv/bin/python -m scripts.export_console_openapi`. Then run
`test_workspace_planner.py`, `test_console_workspace.py`, `test_workspace_contracts.py` and
`test_console_contract_snapshot.py` one at a time. Expected: all PASS.

- [ ] **Step 5: Commit**

`git commit -m "feat(workspace): each turn's language is decided once, carried, saved and returned"`

---

### Task 3: Server answers in the turn's language, with on-screen names

**Files:**
- Modify: `csa/workspace/service.py`:
  - `_TEXT` (469–580): review every `ms` line for English that is not an on-screen name, and
    fix it;
  - the locked-tab branch (~1500);
  - the "Not included" field in `_team_email` (~1358).
- Modify: `csa/workspace/rules.py` (add `TAB_LABELS`)
- Test: `tests/test_workspace_planner.py`, `tests/test_workspace_contracts.py`

**Interfaces:**
- Produces: `TAB_LABELS: dict[str, str]` in `csa/workspace/rules.py`, a copy of the sidebar
  labels in `lib/navigation.ts` for each `TabKey` (for example `"hr": "People & HR"`,
  `"email": "Email assistant"`).
- `_TEXT["en"]` and `_TEXT["ms"]` gain `"not_included"` and `"no_work_email"`.

- [ ] **Step 1: Write the failing tests**

- **`test_tab_labels_match_the_console_navigation`:** reads
  `CSA_WebConsole/csa-console/web/lib/navigation.ts` and, for each
  `{ key: "<k>", label: "<l>"` pair with `k` in `TAB_LABELS`, asserts
  `TAB_LABELS[k] == l`.
- **`test_text_tables_have_the_same_keys`:** asserts `_TEXT["en"].keys() == _TEXT["ms"].keys()`.
- **Locked-tab tests (Docker):** extend the existing tests to assert the answer names
  `"People & HR"`, not `"hr"`, in both languages. In Malay the answer starts `"Tab People & HR"`.
- **Team email test:** extend the existing Malay case to assert the field is
  `CardField(label=_TEXT["ms"]["not_included"], value=f"{names} ({_TEXT['ms']['no_work_email']})")`.

- [ ] **Step 2: Run them and check they fail**

Expected: FAIL (raw `hr` in the answer, missing keys).

- [ ] **Step 3: Implement**

- **Locked-tab text:** format it with `tab=TAB_LABELS.get(proposal.tab, proposal.tab)`.
- **Package names:** keep `BY_KEY[k].title`, since it is the Services page's own name.
- **"Not included" field:** build its label and value from `_TEXT[facts.language]`.

- [ ] **Step 4: Run and check they pass**

Run both files one at a time. Expected: PASS.

- [ ] **Step 5: Commit**

`git commit -m "feat(workspace): Malay answers name tabs as the console shows them"`

---

### Task 4: Cards are written in the turn's language and carry it

**Files:**
- Modify: `csa/workspace/registry.py`:
  - `CardContent` (81): add `language: Language = "en"`;
  - `ProposalExecutor.card` protocol;
  - `prepare` (170);
  - the card dict in `insert` (205);
  - `_out` (443).
- Modify: `csa/workspace/contracts.py` (`ProposalCard.language: Language = "en"`)
- Modify: `csa/workspace/executors.py`:
  - every `card()` (training 131, export 304, send 385, inbox 443);
  - `send_card`, `_content_fields` and `_audience`, which take `language`.
- Modify: `csa/subscriptions/executor.py:290` (accept `language`, keep English text)
- Modify: `csa/workspace/service.py`: `_draft_card` and the `registry.prepare` call (~1534)
  pass `language=facts.language`. `_compose`, `_reply` and `_team_email` pass it through.
- Regenerate: the contract snapshot.
- Test: `tests/test_workspace_registry.py`, `tests/test_workspace_planner.py`

**Interfaces:**
- Consumes: `Language` (Task 1) and `facts.language` (Task 2).
- Produces:
  - `ProposalRegistry.prepare(..., *, origin, language: Language = "en")`;
  - `executor.card(..., *, origin, language: Language = "en")`;
  - the card JSON key `"language"`, and `ProposalCard.language` on every `ProposalOut`.
- The confirm-time re-check in the registry (~310) calls `card()` with the default, and its
  content is discarded.
- The language never enters `payload` or `payload_sha256`.

- [ ] **Step 1: Write the failing tests**

- **`test_malay_send_card_is_malay_and_keeps_the_sentinels`** (planner file, Docker):
  1. a Malay compose turn that asks to send;
  2. assert `card["language"] == "ms"`, and that the title and summary contain no English
     template words ("Send", "We send it", "Nothing is sent");
  3. assert `fields == [{"label": "Subject", "value": "(shown on open)"}, {"label": "Draft", "value": <id>}]`;
  4. assert `'@' not in json.dumps(card)`;
  5. run `deploy/rollout_checks/0485.sql` against the test database and assert it prints `0`.
- **`test_malay_export_card`:** the same checks for a draft (non-send) Malay compose.
- **`test_malay_training_card`:** training title, summary and labels are Malay, and the user's
  title value is unchanged.
- **`test_tab_origin_card_is_english`** (registry file): a card made through the REST propose
  route has `language == "en"`, and its title matches today's English text exactly.
- **`test_card_language_not_in_payload_hash`** (registry file): the same payload prepared in
  `en` and `ms` gives the same `payload_sha256`.

- [ ] **Step 2: Run them and check they fail**

Expected: FAIL (`language` missing, English titles).

- [ ] **Step 3: Implement**

- **Card text:** a module-level `_CARD_TEXT: dict[Language, dict[str, str]]` in `executors.py`
  holds every title, summary and visible label template, both languages with the same keys.
  Add a key-equality test to the registry file.
- **Inbox decision values:** the visible "Decision" value is translated ("Lulus"/"Tolak").
  `payload` keeps `approve`/`reject`.

- [ ] **Step 4: Regenerate the contract, run and check they pass**

Run the planner, registry, contracts and snapshot files one at a time, plus
`tests/test_email_scrub.py`. Expected: all PASS.

- [ ] **Step 5: Commit**

`git commit -m "feat(workspace): assistant cards are written in the turn's language and carry it"`

---

### Task 5: Console copy table and view helpers in two languages

**Files:**
- Create: `CSA_WebConsole/csa-console/web/lib/assistant-copy.ts`
- Modify: `lib/workspace-api.ts`: add `language?: Language` to `WorkspaceTurn` and
  `ProposalCard`, and add `"hr_training_not_permitted"` to `WorkspaceErrorCode` if it is
  missing.
- Modify: `lib/assistant-view.ts`: `navigateText`, `turnErrorText`, `resultView`,
  `resultSummary` and `candidateFollowUp` (unchanged: protocol).
- Modify: `lib/email-recipients.ts`: `recipientGroups`, `emailErrorSentence`, `emailCardBlock`
  and `OUTSIDE_LABEL`.
- Test: `tests/assistant-view.test.mjs`, `tests/email-recipients.test.mjs`, and a new
  `tests/assistant-copy.test.mjs`.

**Interfaces:**
- Produces, in `lib/assistant-copy.ts`:
  - `export type Language = "en" | "ms"`
  - `export function languageOf(x: { language?: Language } | null | undefined): Language` (default `"en"`)
  - `export const PANEL: Record<Language, Record<PanelKey, string>>`. Its keys cover every dock,
    card and status line listed in spec Goal 4.
  - `export function panelText(language: Language, key: PanelKey, vars?: Record<string, string | number>): string`
  - `export function errorLine(cause: unknown, language: Language): string`. For `"en"` it
    returns exactly what the panel shows today. For `"ms"` it uses `MS_ERRORS[code]` (keyed by
    `ApiError.code`), then the Malay email sentence, then the Malay 5xx line for status ≥ 500,
    then the Malay fallback "Sesuatu tidak kena. Cuba lagi."
- Changed signatures, each with a new last parameter `language: Language = "en"` so existing
  callers are unchanged:
  - `navigateText(tab, resume, me, packages, language)`
  - `turnErrorText(cause, language)` (delegates to `errorLine`)
  - `resultView(result, language)`
  - `resultSummary(proposal, language)`
  - `recipientGroups(draft, language)`
  - `emailErrorSentence(error, language)`
  - `emailCardBlock(..., language)`
- `MS_ERRORS` covers:
  - every key of the email `sentences` table, and `send_limit_reached` with its minutes;
  - these `api.ts` codes: `package_inactive`, `proposal_stale`, `proposal_consumed`,
    `proposal_expired`, `proposal_session_mismatch`, `agent_disabled`, `planner_unavailable`,
    `mailbox_not_connected`, `planner_invalid_output`, `email_model_unavailable`,
    `hr_model_unavailable`, `email_timeout_check_saved_state`,
    `hr_agent_timeout_check_saved_state`, `training_not_draft`, `stale_version`,
    `invalid_transition`, `csrf_required`, `no_employee_link`, `hr_training_not_permitted`,
    `reply_target_unavailable`, `email_recipient_invalid`, `recipients_invalid`,
    `draft_rejected`, `thread_body_unavailable`, `resource_unavailable`,
    `reply_approval_not_found`.
- Tab names inside lines come from the existing `tabLabel`/`tabName`, never translated.

- [ ] **Step 1: Write the failing tests**

- **`assistant-copy.test.mjs`:**
  - `PANEL.en` and `PANEL.ms` have identical key sets, and no `ms` value is empty;
  - `MS_ERRORS` has every code listed above;
  - `errorLine` for an `ApiError`-like `{ code: "planner_unavailable", status: 503 }` in `"ms"`
    is the Malay line;
  - an unmapped code in `"ms"` gives the Malay fallback;
  - `{ status: 502, code: "Request failed (502)" }` in `"ms"` gives the Malay 5xx line;
  - every `"en"` result equals today's English output for the same input.
- **`assistant-view.test.mjs`:**
  - `navigateText("hr", false, me, packages, "ms")` contains `tabLabel("hr", me, packages)` and
    no `"Opened"`;
  - `resultSummary` for executed `email_send` in `"ms"` is Malay;
  - `resultView` for an empty directory result in `"ms"` is the Malay "no matching colleague"
    line;
  - every existing English assertion is unchanged.
- **`email-recipients.test.mjs`:**
  - `recipientGroups(draft, "ms")` labels are Malay and the `OUTSIDE` label is Malay;
  - `draftIdFromCard` and `cardFieldsShown` on a Malay card (sentinels kept, Malay visible
    labels) behave exactly as on an English card.

- [ ] **Step 2: Run them and check they fail**

Run: `npm test` (from `CSA_WebConsole/csa-console/web`). Expected: FAIL (module missing).

- [ ] **Step 3: Implement the interfaces above**

The English strings move into `PANEL.en` unchanged, character for character.

- [ ] **Step 4: Run them and check they pass**

`npm test`, `npm run lint`, `npm run typecheck`. Expected: all pass.

- [ ] **Step 5: Commit**

`git commit -m "feat(console): assistant panel copy and error lines in English and Malay"`

---

### Task 6: The panel and card components use the language

**Files:**
- Modify: `components/console/AssistantDock.tsx`:
  - each turn's lines use `languageOf(turn)`;
  - panel-wide lines ("On <tab>", notices, the failed-turn line) use the newest turn's
    language, else `"en"`;
  - "Loading your conversation…" is always English.
- Modify: `components/console/ProposalCard.tsx`: buttons, busy labels, status line,
  "Expires …", error lines and the email block use `languageOf(card)`.
- Modify: `components/console/EmailPreview.tsx`: a `language` prop, default `"en"`.
- Test: the helpers are covered in Task 5. Add a test only for any new pure helper.

**Interfaces:**
- Consumes: everything Task 5 produces.
- Produces: `latestLanguage(turns: WorkspaceTurn[]): Language` in `lib/assistant-view.ts`. It is
  the newest turn's language, else `"en"`.

- [ ] **Step 1: Write the failing test**

`latestLanguage` returns:
- `[]` → `"en"`;
- `[{language:"ms"}]` → `"ms"`;
- `[{language:"ms"},{language:"en"}]` (oldest first) → `"en"`;
- a turn without `language` → `"en"`.

- [ ] **Step 2: Run it and check it fails**

`npm test`. Expected: FAIL.

- [ ] **Step 3: Implement `latestLanguage` and wire the three components**

Wire the components as listed under Files. Other callers of `ProposalCard` and `EmailPreview`
(the HR, Inbox, Subscriptions and Email tabs) need no change.

- [ ] **Step 4: Run it and check it passes**

`npm test`, `npm run lint`, `npm run typecheck`. Expected: all pass.

- [ ] **Step 5: Commit**

`git commit -m "feat(console): the assistant panel and its cards follow the conversation's language"`

---

### Task 7: Smoke cases, checklist and docs

**Files:**
- Modify: `csa/workspace/smoke_cases.json`: add `compose-ms-addr` (the Goal 1 address
  sentences, expecting `email_compose` to the exact address) and `navigate-ms-hr`
  (`"buka tab HR"`, expecting `navigate` to `hr`), both `language: "ms"`.
- Modify: `docs/ASSISTANT_RETEST.md`:
  - remove "(console error sentences are English only)" from the Malay rows, and expect Malay
    sentences;
  - add a carry-on row: a Malay request, then only an address typed, then a Malay Send card;
  - add a row where a Malay card shows Malay buttons and status.
- Modify: `docs/CONSOLE_API.md`: document `WorkspaceTurnOut.language` and
  `ProposalCard.language` (both `"en" | "ms"`, default `"en"`).
- Modify: `docs/PROGRESS.md`: add a top entry for this branch, in the style of the
  assistant-audit entry, with test counts.
- Test: `tests/test_assistant_smoke.py` (the case file loads and its ids are unique).

- [ ] **Step 1:** add the two cases, then run `tests/test_assistant_smoke.py`. Expected: PASS.
- [ ] **Step 2:** edit the three docs as listed.
- [ ] **Step 3:** run `make lint`. Expected: clean.
- [ ] **Step 4: Commit**

`git commit -m "docs(workspace): Malay smoke cases, retest rows and the language fields"`
