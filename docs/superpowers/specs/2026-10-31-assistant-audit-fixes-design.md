# Workspace assistant: audit fixes, retest checklist and model smoke run

**Date:** 2026-10-31. **Base:** main `8f3d6f0`.

**Owner approvals (2026-10-31):**
- the split: all ten must-fix items in one branch, the should-fix items later;
- the retest checklist plus the optional model smoke run.

## Intent

The owner's staging tests keep finding assistant failures, one feature at a time:
- a 500 error on "send email to danial@fourstacks.my …";
- "That item isn't available any more" on a training card;
- a dead end at "No matching colleague was found".

A read-only audit of main `8f3d6f0` showed these come from three patterns, not single bugs:
1. **AI output is not held to a contract.** Prompts do not name the JSON keys, model failures are not caught, and the error codes have no console text.
2. **Prerequisites are checked only at execute.** The user learns at Confirm, from a misleading message.
3. **Empty results drop the request.** Nothing offers a next step.

The deeper cause is that every test uses a scripted model, so real-model behaviour is only ever tested by hand.

The aim is to fix every instance of the three patterns the audit found. It also gives the owner a repeatable way to retest:
- a written checklist;
- an owner-triggered smoke run that sends the same prompts through the real model on staging.

## Goals (the ten must-fix items, M1–M10)

**M1: No 500 when a model fails while drafting an email.**
- `csa/workspace/service.py`, `create_planned_turn` catches `CompletionError` (from `csa.decide`) and `TimeoutError` raised while resolving a plan. Today these escape from `_compose`, `_reply` and `_team_email`.
- They become `HTTPException(503, "email_model_unavailable")` when the plan is an email action. Otherwise they become the existing `PlannerUnavailable`.
- The turn route already maps `PlannerError`.

**M2: Model output contracts.**
- **Prompts.** `csa/email/service.py`, `_compose_with_model` and `_draft_with_model` each end their system prompt with exactly this line: `Return one JSON object with exactly these keys: subject (string, at most 200 characters), body (string), needs_facts_from_staff (list of at most 5 short strings, empty if none). No other keys.`
- **`DraftProposal`** (`csa/email/models.py`):
  - it gets its own `ConfigDict(extra="ignore")`. Recipients are never taken from the model; `to` is computed server-side;
  - a `mode="before"` validator cuts `subject` to 200 characters and `needs_facts_from_staff` to 5 items.
  - The saved draft is validated again as `EmailDraftInput`, as today.
- **HR chat** (`csa/hr/assistant.py`): the system prompt states the wrapper exactly: `Return one JSON object: {"action": <one tool call object> or null, "clarification": "request_details" | "manual_decision_required" | "unsupported_request" or null}; exactly one of them is non-null.` Adjust the wording to the actual `HrChatPlan` fields, which the builder reads from the model.

**M3: Every error code a user can reach has console text** (`CSA_WebConsole/csa-console/web/lib/api.ts`, `explained`, plus `lib/email-recipients.ts` for email cards).
- **Codes to add:**
  - `planner_invalid_output`
  - `email_model_unavailable`
  - `hr_model_unavailable`
  - `email_timeout_check_saved_state`
  - `hr_agent_timeout_check_saved_state`
  - `training_not_draft`
  - `stale_version`
  - `invalid_transition`
  - `csrf_required`
  - `provider_unreachable` (email)
  - `permission_denied` (email)
  - `no_employee_link` (new, M4)
  - `hr_training_not_permitted` (new, M4)
- **Text:** one plain sentence each, saying what happened and what to do. For example: "The assistant couldn't produce a usable answer. Try again or rephrase."
- **Non-JSON error bodies:** a 500 with a body that isn't JSON shows "Something went wrong on our side. Try again." instead of "Request failed (500)".

**M4: Training prerequisites are checked when the card is made** (`csa/workspace/executors.py`, `TrainingSubmitExecutor.card`).
- **The card check.** For a new request, call `HrService.context(session)`, which already enforces the session and `hr_assistant`:
  - when `employee is None`, raise `HTTPException(409, "no_employee_link")`;
  - when neither `training_draft` nor `training_submit` is in its actions, raise `HTTPException(403, "hr_training_not_permitted")`.
  - The same check guards the existing-request path: submitting needs the submit grant.
- **`HrService.save_draft`** raises `HrPolicyError("no_employee_link")` for a missing employee link, replacing `resource_unavailable`. `hr_http_error` maps it to 409 `no_employee_link`.
  - This is not an enumeration risk: it concerns the caller's own record.
  - Other `resource_unavailable` uses stay as they are.
- **New planner fact** `my_employee: {linked: bool, can_request_training: bool} | None`. It is computed only when the message has a training word, the same way as `_MANAGER_WORDS`; the builder chooses the word list, with English and Malay words.
- **New prompt line:** `If my_employee.linked is false or can_request_training is false, answer that training requests need a linked employee record with training access and do not propose training_submit.`
- **Orphan drafts:** with the card check in place, a refused submit can no longer leave a draft behind for this reason.

**M5: Directory dead end** (`_resolve` / `_finish` in `service.py`).
- **When it applies:** `directory_lookup` finds 0 candidates, or HR is inactive, and the request is about email (`_about_email` on the authored parts).
- **What the user gets instead:** a clarification with no options:
  - en: `I couldn't find that person in the directory. Type their email address and I'll continue.`
  - ms: `Saya tidak menjumpai orang itu dalam direktori. Taip alamat e-mel mereka dan saya akan teruskan.`
- **Why the send survives:** the typed answer folds in the first request (existing `typedMessage` / `_authored`), so "send" still counts.
- **Outside email:** today's text, localised. ms: `Tiada rakan sekerja yang sepadan ditemui.`

**M6: Turns without a tab are no longer ignored** (`AssistantDock.tsx` `ask`, `lib/workspace-api.ts`).
- When the route has no tab context, the dock sends `page_context: {tab: "overview"}`.
- Nothing sends a planned turn without `page_context` any more. The legacy no-context path stays for old clients.

**M7: Planner contract violations get one correction, then a plain answer** (`_plan` in `service.py`).
- **Trigger:** `_resolve` raises `PlannerInvalidOutput` for a reason the server can name:
  - a recipient not in the user's words (`_check_recipients`);
  - a thread not in `mailbox_threads`;
  - a blank directory query;
  - an over-long option;
  - a contract `model_validator` failure, such as both or neither of `recipient_employee_id` / `recipient_address`.
- **Correction:** re-plan once with a fixed server note naming the rule. Each reason has its own fixed text, written by the builder. The correction is never user or mail text.
- **If the second plan also violates a rule:**
  - an unknown thread is answered with "I couldn't find that conversation in your synced email." (and ms);
  - every other reason is answered with "I couldn't work out who to send that to. Type their email address." (and ms).
- **Shared budget:** at most two planner calls per turn, shared with the content correction from #56. A turn already corrected is never corrected again.

**M8: Navigation is never contradictory.**
- The server answers with the locked text instead of `navigate` when the target tab is not available to this user.
- It uses the same rule as the console's `navigationFor` / `tabHref`: package, role and HR access. The builder ports the rule and states it in one place on the server.
- The dock no longer prints "Opened X." when `tabHref` returns null; it shows only the notice.

**M9: Email cards check the mailbox is connected** (`email_export` and `email_send` `card()`). A mailbox that isn't connected raises 409 `mailbox_not_connected` when the card is made, not at Confirm. The console text for it already exists.

**M10: Attention summary on a never-synced mailbox.**
- When the mailbox is connected but has never synced (no `last_synced_at`, or no synced threads), the answer is:
  - en: `Your email hasn't been synced yet. Open the Email assistant tab and press Sync, then ask again.`
  - ms: `E-mel anda belum disegerakkan. Buka tab Pembantu E-mel dan tekan Segerak, kemudian tanya semula.`
- The builder decides "never synced" from the mailbox row.

## Process additions

**P1: Retest checklist** (`docs/ASSISTANT_RETEST.md`).
- One table per feature, in English and Malay. Each row holds the prompt, any setup needed and the expected result.
- **Features:** navigate, answer, directory, compose/send, draft, reply, team, attention, training, inbox decision.
- **Failure paths** each get a row:
  - no directory match;
  - no employee link;
  - no mailbox;
  - an address in the request;
  - "don't send".
- **When it is used:** after each rollout. The PR template is left alone; the runbook "Routine rollouts" section points at the checklist.
- **One source of truth:** the rows come from the same data file as P2, so the two cannot drift. The doc is generated from it, or a test checks that they agree.

**P2: Model smoke run** (`scripts/assistant_smoke.py`, owner-triggered, never CI).
- **Where it runs:** inside the staging gateway container, with `docker exec csa-staging-gateway-1 python -m scripts.assistant_smoke`. The gateway already has the model key mounted, so the script needs no credentials.
- **What it calls:** the real planner (`WorkspacePlanner.plan`) and the real email drafting call (`_compose_with_model`). The input is synthetic `PlannerFacts` built from the case file. No database, Gmail, tenant data, writes or HTTP routes are involved.
- **Case file:** `csa/workspace/smoke_cases.json`. Each case holds the message, the facts preset and the expectation: the allowed actions, the action that must not appear, whether `send` is expected, that the clarification has no placeholder options, and that the draft is valid.
- **What it checks:** each output against the expectation. It applies the same server-side filters the turn path uses (placeholder options, the send rule, the content check), as pure functions.
- **What it prints:** pass/fail per case and the action chosen, with no prompts or model text beyond the action name and the field names. It exits non-zero on any failure.
- **Cost:** about 30–40 model calls per run.
- **Runbook:** it is documented in `deploy/STAGING_PIPELINE.md` next to the rollout script.
- **Unit tests** run it with a scripted model, so CI never calls the provider.

## Non-goals

- The should-fix items S1–S5: directory job-title search, prompt gaps, Malay strings beyond those named here, an automatic retry on invalid output, and the 10-result hint and token limit. They are planned separately.
- No migration.
- No change to recipient trust (`_authored`, `_requested_addresses`, `_offered_employee_ids`, `_check_recipients`).
- No change to the `email_send` confirm, limit or binding.
- No new provider.

## Approaches

**A. Fix by pattern in one branch, plus the checklist and smoke run (chosen).** Each pattern is fixed everywhere the audit found it, and the smoke run is what catches the next real-model drift.

**B. Per-feature PRs.** These are smaller, but they repeat the same fix in many places and stretch the retest cycle across many rollouts.

**C. Replace JSON mode with tool or function calling.** This is stronger typing, but a provider and contract change across every call; parked.

## Constraints

**Tests**
- Synthetic fixtures and testcontainers only. No test calls Groq, Gmail or staging.
- Locally, run only the changed files' tests and `make lint`. The full suite runs in PR CI.

**Shared files**
- `planner.py` is shared with other sessions: additive changes only, except the prompt lines named here.
- The `hr` and `email` service changes keep their public signatures.

**Language**
- Each fixed string the user sees and that is named here has English and Malay versions, chosen by `facts.language`.

## Left to the builder

- Training word lists, M7 note texts, the exact console sentences (one plain sentence each), and the smoke case list (at least two cases per feature, English and Malay).
- How `ASSISTANT_RETEST.md` is kept in step with `smoke_cases.json`.

## Settled after the builder check (assistant's calls, 2026-10-31)

1. **One correction per turn, whatever its kind.**
   - **The loop:** a turn-level helper wraps plan plus resolve.
     - The first plan is resolved. If it asks for content (#56) or breaks a rule M7 can name, the turn is re-planned once with that correction's note.
     - The second plan is final:
       - if it asks for content, it gets the content fallback question;
       - if it breaks a rule, it gets the M7 fallback answer;
       - otherwise it resolves normally.
   - **Reads:** a re-plan repeats `_resolve`'s directory and thread reads. They are cheap and read-only.
   - **Reasons:** `PlannerInvalidOutput` gains an optional reason code that the server sets. An unknown reason gets no correction and stays a 502, which now has console text (M3).
2. **`my_employee` when HR isn't usable.**
   - When a training word appears, the fact is always present, as `{linked, can_request_training, reason}`.
   - `reason` is one of `hr_inactive`, `no_hr_access`, `no_employee_link` or null.
   - The prompt line covers any false value: answer with the reason, and do not propose.
   - **Card check:**
     - an inactive HR package keeps today's 403 `package_inactive`, which already has console text;
     - `permission_denied` from `HrService.context` becomes 403 `hr_training_not_permitted`;
     - every other HR policy code goes through `hr_http_error`, and M3 covers the codes that can be reached.
3. **M8 scope.**
   - The server checks package and role, which it already knows, and answers with the locked text when either blocks the tab.
   - HR-access gating stays in the console. With the dock fix (no "Opened X." when `tabHref` is null), only the notice shows.
   - No HR call is made on navigate turns, so nothing can fail open or closed.
   - The rule is the same for platform admins.
4. **M10.** "Never synced" means the mailbox row's last-sync time is null. Zero threads after a sync is a normal answer.
5. **P2 strictness.**
   - Each case is sampled once by default, with `--repeat N` to report a pass rate.
   - Expectations are allowed-action sets, not single actions.
   - The exit code is 1 when any case fails, and a summary is printed.
   - **Pure functions:** the server rules the smoke run needs (placeholder filter, send rule, content check) may move into a new module `csa/workspace/rules.py`, imported by `service.py`. That is a refactor; behaviour does not change.
   - **The email-drafting call** is reached through `EmailService` built without a pool. If that is awkward, it is reached through a small, pure prompt-plus-model function extracted from `_compose_with_model`.
   - **The model key:** the staging gateway does mount the real model key. `deploy/staging-rollout.env` sets `CSA_STAGING_GATEWAY_MODEL_SECRET=staging_model_key`.
6. **P1 shape.**
   - `smoke_cases.json` holds only the cases a model can be tested on, each with an `id`.
   - `docs/ASSISTANT_RETEST.md` is a hand-written superset. It has state-dependent rows (no mailbox, no employee link, no directory match), English and Malay as separate rows, and each model-testable row cites its smoke case `id`.
   - A unit test checks that every smoke case `id` appears in the checklist.
7. **M1.** A model failure while planning stays `PlannerUnavailable` (503 `planner_unavailable`). Only a failure in the email-drafting call inside resolve becomes 503 `email_model_unavailable`.
