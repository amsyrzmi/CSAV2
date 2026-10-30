# Assistant Clarify and Send Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The server enforces three things the planner prompt only asked for: questions that need typing have no placeholder options, there are no subject or wording questions (one corrective re-plan, then a fixed question), and "send …" gives a Send card. Old question cards in the console can no longer be clicked.

**Architecture:**
- **Server:** filtering and the re-plan live in `csa/workspace/service.py`, around the existing `_clarification` / `_asked_to_send` helpers and the turn handler.
- **Planner:** `csa/workspace/planner.py` gains an optional server `note`; the change is additive.
- **Console:** `AssistantDock.tsx` only lets the newest turn's buttons answer, through a pure helper in `lib/assistant-view.ts`.

**Tech Stack:** Python 3.12, FastAPI, pytest with testcontainers (`requires_docker`), Next.js/TypeScript, node test runner (`tests/*.test.mjs`).

**Spec:** `docs/superpowers/specs/2026-10-30-assistant-clarify-send-design.md`

## Global Constraints

**Placeholder options**
- An option is dropped when either:
  - its label (stripped, case-insensitive) starts with a whole word from `enter|type|provide|input|write|specify|fill in|masukkan|taip|berikan|isi`; or
  - its value matches `^[a-z]+(?:_[a-z]+)+$`.

**Content questions**
- **Detection:** a plan asks for content when it is a clarification whose question matches `\b(subject|body|wording|content|tajuk|isi|kandungan)\b` (case-insensitive).
- **Correction note, verbatim:** `Do not ask for the subject or the wording. Write them yourself from the request; if the request says nothing about the content, write a short, neutral email from what it does say.`
- **Fallback questions, verbatim, with no options:**
  - en: `What should the email say? Type a line or two and I'll write it.`
  - ms: `Apa yang patut e-mel itu katakan? Taip satu atau dua ayat dan saya akan menulisnya.`

**Prompt changes in `planner.py`**
- `planner_prompt` adds `\n<correction>\n{note}\n</correction>` after `</message>`, only when a note is given.
- New system line, verbatim: `A <correction> block, when present, is from the server: follow it.`
- The `send` line, new text verbatim: `- send (on email_compose, email_reply and email_team): true when the user asks to send or to email someone ("send an email to…", "email Danial and tell him…"); false when they ask for a draft ("draft", "write", "save"). Either way the user confirms a card first.`

**Send rule** (on `_authored` parts; the first match wins)
1. `\b(draft|draf|save|simpan)\b` gives a draft card.
2. `_SEND_WORDS` gives a Send card.
3. `proposal.send` and `\b(email|e-?mel|emel)\b` and not `\bwrite\b` gives a Send card.
4. Anything else gives a draft card.

**Unchanged:** `_authored`, `_requested_addresses`, redaction, `email_send` confirm/limit/binding, and migrations (none).

**Process**
- `planner.py` is shared with other sessions: additive changes only, plus the reworded `send` line.
- **Running tests locally:** one file per command through `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/7d09c8ff-ee80-4525-b21a-195503f98d9c/scratchpad/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest <file> -q`, plus `make lint`. Web: `npm run lint`, `npm run typecheck`, `npm test` in `CSA_WebConsole/csa-console/web`. No `VAR=` prefixes and no chained commands.

## Review Focus

1. **An option the user really needs is removed.** Real choices such as "Enterprise plan", "First aid", a colleague (employee id) or a thread id must survive; only whole-word "Enter …"/"Type …"-style labels and snake_case codes go. Test: `test_placeholder_filter_keeps_real_options`.
2. **The retry loop never repeats.** At most two planner calls per turn. Test: `test_content_question_falls_back_after_one_correction` asserts exactly 2 calls.
3. **A clicked option "send it now" still never makes a Send card.** Test: `test_send_rule_follows_the_users_words` includes a clicked-value case.
4. **"save it as a draft, don't send" stays a draft.** Same test.
5. **The text box always answers the newest clarification, even though older buttons are disabled.** Test: `isAnswerable` unit test, plus the existing `typedMessage` tests still passing.

---

### Task 1: Server enforcement (options, content re-plan, send rule) and docs

**Files:**
- Modify: `csa/workspace/service.py`: `_clarification`, `_asked_to_send`, the turn handler call `planner.plan(turn.body, facts)` (≈ line 1450), plus new helpers.
- Modify: `csa/workspace/planner.py`: `planner_prompt`, `WorkspacePlanner.plan`, `SYSTEM_PROMPT` (the `send` line and the new correction line).
- Modify: `tests/test_workspace_planner.py`.
- Modify: `docs/EMAIL_ASSISTANT.md` and `docs/PROGRESS.md` (one new first entry).

**Interfaces:**
- Produces:
  - `planner_prompt(message: str, facts: PlannerFacts, note: str | None = None) -> str`
  - `WorkspacePlanner.plan(message, facts, *, note: str | None = None)`
  - in `service.py`: `_is_placeholder(option: ClarificationOption) -> bool`, `_asks_for_content(proposal: WorkspaceProposal) -> bool`, `async _plan(planner, message: str, facts: PlannerFacts) -> WorkspaceProposal`
- **Test doubles:** any fake planner used by the tests must accept the `note` keyword. Update the test doubles in `tests/` that subclass or replace `WorkspacePlanner.plan`; grep for them.

- [ ] **Step 1: Write the failing tests** in `tests/test_workspace_planner.py`

```python
def test_placeholder_filter_keeps_real_options() -> None:
    # dropped: ("Enter address","enter_address"), ("Provide subject and body","subject_body"),
    #          ("Type the address","x"), ("Masukkan alamat","alamat")
    # kept: ("Enterprise plan","enterprise"), ("First aid","first aid"),
    #       ("Danial Ihsan", "<uuid>"), ("Re: test signature","<thread id>")
def test_clarification_drops_placeholder_options() -> None:
    # _clarification(ClarificationProposal(question="Email address?", options=[Enter address/enter_address]))
    # -> both returned results have options == []
def test_content_question_detection() -> None:
    # True: "What should be the subject and body of the email to Danial?", "Apakah tajuk e-mel?"
    # False: "Which Danial do you mean?", "Please provide the full email address for Danial."
def test_send_rule_follows_the_users_words() -> None:
    # Send: "send email to danial"; "hantar emel kepada danial";
    #       proposal.send=True + "email danial and tell him the meeting moved"
    # Draft: "draft an email to danial"; "save it as a draft, don't send";
    #        proposal.send=True + "write an email to danial";
    #        proposal.send=False + "email danial"; a clicked option value "send it now" (latest
    #        offered_options contains it)
@requires_docker
async def test_owner_flow_types_the_address_and_gets_a_send_card(planned) -> None:
    # 1 model: clarification "Please provide the full email address for Danial." options
    #   [{"label":"Enter address","value":"enter_address"}] for "send email to danial@fo..."
    #   -> result options == []
    # 2 typed "danial@example.test\n(Answer to “<q>” for: send email to danial@fo...)":
    #   model: clarification "What should be the subject and body of the email to Danial?"
    #   then (re-plan) email_compose to danial@example.test, subject "Hello", instructions "Say hello"
    #   -> proposal kind == "email_send"; len(model.calls) for this turn == 2;
    #      the second call's user prompt ends with the <correction> block containing the note
@requires_docker
async def test_content_question_falls_back_after_one_correction(planned) -> None:
    # model asks a subject question twice -> result is clarification with the en fallback text,
    # options == [], exactly 2 model calls for the turn
def test_planner_prompt_carries_the_server_note() -> None:
    # planner_prompt(m, facts, note="X").endswith("</message>\n<correction>\nX\n</correction>")
    # planner_prompt(m, facts) has no "<correction>"; SYSTEM_PROMPT contains both verbatim lines
```

Update the existing tests that expected `email_export` for a request containing "send" with no `send` flag, such as `test_typed_answer_carries_the_address_into_a_draft`. Each now expects `email_send`, or its request changes to "email …" where the test is about drafts. Name every changed test in the report.

- [ ] **Step 2: Run to verify they fail**

Run: `bash …/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_workspace_planner.py -q`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement** per the Global Constraints: the filter inside `_clarification` (before masking), `_asks_for_content`, `_plan` (used by the turn handler instead of `planner.plan`), the `note` in `planner.py`, the new `_asked_to_send` rule, and the prompt lines. Then update `docs/EMAIL_ASSISTANT.md` (the send rule, and the guarantees about clarification options and content questions) and add a `docs/PROGRESS.md` first entry.

- [ ] **Step 4: Run to verify they pass**

Run the same file, then `tests/test_workspace_contracts.py` and `tests/test_workspace_registry.py` (each on its own), then `make lint`.
Expected: all pass, and lint is clean.

- [ ] **Step 5: Commit**

```bash
git add csa/workspace/service.py csa/workspace/planner.py tests/test_workspace_planner.py docs/EMAIL_ASSISTANT.md docs/PROGRESS.md
git commit -m "fix(workspace): server drops placeholder options, corrects content questions, sends on send"
```

---

### Task 2: Only the newest card answers (console)

**Files:**
- Modify: `CSA_WebConsole/csa-console/web/lib/assistant-view.ts` (new export)
- Modify: `CSA_WebConsole/csa-console/web/components/console/AssistantDock.tsx` (the `turns.map` call and `TurnResult`)
- Modify: `CSA_WebConsole/csa-console/web/tests/assistant-view.test.mjs`

**Interfaces:**
- Produces: `export function isAnswerable(turns: WorkspaceTurn[], turnId: string): boolean`. It returns true only when `turnId` is the id of `turns.at(-1)`.
- `TurnResult` gains `answerable: boolean`. The clarification option buttons and the candidate buttons use `disabled={disabled || !answerable}`.

- [ ] **Step 1: Write the failing test** `isAnswerable`: for three turns only the last id is true; an empty list or an unknown id is false.
- [ ] **Step 2: Run** `npm test` in the web directory. Expected: FAIL (no export).
- [ ] **Step 3: Implement** the helper and the wiring: `answerable={isAnswerable(turns, turn.id)}`.
- [ ] **Step 4: Run** `npm run lint`, `npm run typecheck`, `npm test`. Expected: all pass.
- [ ] **Step 5: Commit**

```bash
git add CSA_WebConsole/csa-console/web/lib/assistant-view.ts CSA_WebConsole/csa-console/web/components/console/AssistantDock.tsx CSA_WebConsole/csa-console/web/tests/assistant-view.test.mjs
git commit -m "fix(console): only the newest assistant card can answer"
```
