# Workspace assistant: clarifications the server enforces, and "send" that sends

Short design, email track. Date: 2026-10-30. Base: main `968378b`. Owner approved 2026-10-30 ("go ahead with that").

## Intent

The owner tested "send email to danial" on staging, which runs PR #54. The assistant still looped, then saved a Gmail draft instead of sending. The transcript shows four causes:

1. **Fake options.** The model offered "Enter address" (value `enter_address`) and "Enter subject and body" (value `subject_body`). Clicking one sends that code as the answer, so the same question comes back.
2. **Subject/body questions.** The prompt rule "never ask for the subject or the wording" was ignored once the address was known. PR #54 relied on the prompt alone.
3. **Stale cards.** An old question card stayed clickable. Clicking it answered an earlier question and dropped the answers given since.
4. **Draft, not send.** "send email to …" gave a draft card. The server requires both the model's `send` flag and a send word from the user, and the model left `send` false.

The address part worked: the typed address reached the draft. `[EMAIL_0]` is only the stored, redacted view.

## Goals

- The server, not the prompt, guarantees that a question needing typing has no options.
- The assistant never ends on a subject or wording question it could have written itself. If the model insists, it is corrected once; after that the user is asked one plain question.
- Only the newest question or candidate card in a thread can be clicked.
- "send …" (or an email verb plus a model `send` flag) gives a **Send** card. "draft…", "write…" or "save…" gives a draft card. The user still confirms every card (owner decision 2026-10-30).

## Non-goals

- No migration, no new scope, and no change to the confirm, limit or binding rules of `email_send`.
- No change to how addresses are counted (`_authored` / `_requested_addresses`) or to redaction.
- No new LLM provider.

## Approaches

**A. Server enforcement plus one corrective re-plan (chosen).** The server filters the plan's output, re-plans once with a fixed server note when the model asks for content, and decides send from the user's own words. It is deterministic where it matters, costs at most one extra model call, and only in the bad case.

**B. Prompt-only fixes.** These are cheap, but PR #54 showed Groq ignores such rules.

**C. Structured address form.** The assistant would stop asking clarifications for email entirely and show a dedicated "To" field. It is cleaner UX, but a bigger change; parked.

## Design

### 1. Placeholder options (`csa/workspace/service.py`, `_clarification`)

Before masking, drop every option where either:

- the label, stripped and case-insensitive, starts with `enter`, `type`, `provide`, `input`, `write`, `specify`, `fill in`, `masukkan`, `taip`, `berikan` or `isi` (whole word); or
- the value matches `^[a-z]+(?:_[a-z]+)+$` (a snake_case placeholder code).

The rest of the clarification stays as is. Dropping all options leaves a plain question; the user types the answer, which already works.

### 2. Content questions get one correction (`service.py` turn handler, `planner.py`)

- **Additive changes to `planner.py`:**
  - `WorkspacePlanner.plan(message, facts, *, note: str | None = None)`. When given, `planner_prompt` appends `\n<correction>\n{note}\n</correction>` after `</message>`. The note is fixed server text, never user or mail text.
  - A new system-prompt line: "A <correction> block, when present, is from the server: follow it."
- **Detection.** A plan *asks for content* when it is a clarification whose question matches `\b(subject|body|wording|content|tajuk|isi|kandungan)\b` (case-insensitive).
- **Handling.** The turn handler calls a new `_plan(planner, message, facts)`:
  1. Plan.
  2. If the plan asks for content, plan once more with the note: `Do not ask for the subject or the wording. Write them yourself from the request; if the request says nothing about the content, write a short, neutral email from what it does say.`
  3. If the second plan still asks for content, return a fixed clarification with no options:
     - en: `What should the email say? Type a line or two and I'll write it.`
     - ms: `Apa yang patut e-mel itu katakan? Taip satu atau dua ayat dan saya akan menulisnya.`
- **Errors.** A planner error on the retry is raised as usual (`PlannerInvalidOutput` / `PlannerUnavailable`).

### 3. Only the newest card answers (console)

- `AssistantDock.tsx` passes `answerable` to `TurnResult`, true only for the thread's last turn.
- Clarification option buttons and directory candidate buttons are disabled when not answerable.
- The text box is unchanged: it always answers the newest clarification (`typedMessage`).
- A small pure helper in `lib/assistant-view.ts`, `isAnswerable(turns, turnId)`, is unit-tested.

### 4. Send from the user's words (`service.py` `_asked_to_send`, planner prompt line)

**Rule.** (Amended by the final review on 2026-10-30: negated send, "drafts", write words `write|tulis|karang` and stale clicks; the plan's Global Constraints hold the final rule.) It applies to the parts `_authored` counts. The first match wins:

1. A draft word appears (`\b(draft|draf|save|simpan)\b`): **draft card**. This also covers "save it as a draft, don't send".
2. A send word appears (the existing `_SEND_WORDS`): **Send card**.
3. The plan's `send` is true, an email verb appears (`\b(email|e-?mel|emel)\b`), and `\bwrite\b` does not: **Send card**.
4. Anything else: **draft card**.

**Planner prompt.** The `send` line becomes: true when the user asks to send or to email someone ("send an email to…", "email Danial and tell him…"); false when they ask for a draft ("draft", "write", "save"). Either way the user confirms a card first.

The planner-written question or clicked option never counts: unchanged, via `_authored`.

### 5. Docs

- `docs/EMAIL_ASSISTANT.md`: the send rule and the clarification guarantees.
- `docs/PROGRESS.md`: one entry.

## Tests

- **Unit tests** (pure functions): placeholder filter, content detection, send rule (send / email + flag / draft words / clicked value never counts).
- **One docker test reproducing the owner's flow** with a scripted model:
  1. The model asks for the address with an "Enter address" option. The returned options are empty.
  2. The user types the address. The model asks for the subject and body; on the corrected re-plan it composes.
  3. The request "send email to …" gives an `email_send` card. Two model calls are made for that turn.
- **A fallback test:** the model asks for content twice. The fixed question is returned with no options.
- **Existing tests:** those expecting `email_export` for "send email to …" with no `send` flag now expect `email_send`. Each change is listed in the report.
- **Web:** a unit test for `isAnswerable`.

## Left to the builder

- Helper names inside `service.py`, beyond those named here.
- Exact test fixtures.
