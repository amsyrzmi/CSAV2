# Assistant Audit Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every instance of the audit's three patterns (M1–M10): AI output with no contract, prerequisites checked only at execute, and empty results that drop the request. Add a retest checklist and an owner-triggered model smoke run.

**Architecture:**
- **Server:** the fixes go where each pattern lives: email drafting (`csa/email`), HR training (`csa/hr` + `csa/workspace/executors.py`), and the workspace turn loop (`csa/workspace/service.py` + a new pure `csa/workspace/rules.py`).
- **Console:** one task for messages and dock fixes.
- **Smoke run:** `scripts/assistant_smoke.py` reuses the pure rules and the real model client against synthetic facts.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, pytest + testcontainers, Next.js/TypeScript (node test runner).

**Spec:** `docs/superpowers/specs/2026-10-31-assistant-audit-fixes-design.md` (including its "Settled after the builder check" section, which is binding).

## Global Constraints

**Security and contracts**
- These stay unchanged: `_authored`, `_requested_addresses`, `_offered_employee_ids`, `_check_recipients`, the `email_send` confirm, limit and binding.
- No migration, no new provider, and no new dependency.
- `planner.py` is shared with other sessions: additive changes only, plus the prompt lines named here. The `hr` and `email` services keep their public signatures.

**User-visible text**
- Every fixed text the user sees and that the spec names has English and Malay versions, chosen by `facts.language`.
- The verbatim strings are in the spec. Use them exactly:
  - the M2 key line;
  - the M5 en/ms texts and the localised "no match" text;
  - the M10 en/ms texts;
  - the M7 fallbacks (en is given; ms is written by the builder in the same style as the M5 ms text).
- New error codes, verbatim:
  - `email_model_unavailable` (503)
  - `no_employee_link` (409)
  - `hr_training_not_permitted` (403)
  - `mailbox_not_connected` (409, existing)

**Correction loop**
- At most two planner calls per turn, shared by the content correction (#56) and the M7 contract correction. The second plan is final.

**Process**
- **Tests:** synthetic fixtures only, and no test calls Groq, Gmail or staging. The smoke script is tested with a scripted model.
- **Running tests locally:** one file per command through `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/7d09c8ff-ee80-4525-b21a-195503f98d9c/scratchpad/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest <file> -q`, plus `make lint`. Web: `npm run lint`, `npm run typecheck` and `npm test` in `CSA_WebConsole/csa-console/web`. Never the full suite. No `VAR=` prefixes and no chained commands.

## Review Focus

1. **A re-planned turn still goes through every recipient and send gate.** Test: `test_correction_replan_keeps_recipient_gates` (Task 3). A second plan with an address the user never wrote is refused, never sent.
2. **The training card for a user with no employee link is refused when the card is made, and no draft row is created.** Test: `test_training_card_requires_employee_link` (Task 2). It asserts 409 `no_employee_link` and zero `hr_training_requests` rows for that user.
3. **An email turn whose drafting model fails gives 503 `email_model_unavailable`, never 500.** The same holds for reply and team. Test: `test_drafting_model_failure_is_503` (Task 1), parametrised over compose, reply and team.
4. **The dock never posts a turn without `page_context`.** Test: `turnInput` / `ask` helper test (Task 4).
5. **The smoke script never prints model text beyond action and field names, and exits 1 on any failure.** Test: `test_smoke_output_is_content_free_and_exit_code` (Task 5).

---

### Task 1: Email drafting contract, no 500, mailbox check on cards, never-synced summary (M1, M2-email, M9, M10)

**Files:**
- Modify: `csa/email/models.py` (`DraftProposal`)
- Modify: `csa/email/service.py` (`_compose_with_model`, `_draft_with_model` system prompts)
- Modify: `csa/workspace/service.py` (the email branches of resolve: `_compose`, `_reply`, `_team_email`, and `_summary`)
- Modify: `csa/workspace/executors.py` (`email_export` and `email_send` `card()`)
- Test: `tests/test_email_contracts.py`, `tests/test_workspace_planner.py`, `tests/test_workspace_registry.py`

**Interfaces:**
- Produces: `HTTPException(503, "email_model_unavailable")` from the workspace email branches, when `csa.decide` `CompletionError` or `TimeoutError` comes from the drafting call. A failure in planning stays `PlannerUnavailable`.

- [ ] **Step 1: Write the failing tests**
  - `test_draft_proposal_ignores_extras_and_trims`: `DraftProposal.model_validate({"subject": "x"*250, "body": "b", "needs_facts_from_staff": ["a"]*7, "to": "Adam"})` gives a subject of 200 characters and 5 facts.
  - `test_draft_prompts_name_the_keys`: both system prompts contain the M2 key line verbatim. Capture them with a scripted model.
  - `test_drafting_model_failure_is_503` (docker, parametrised: compose, reply, team): a scripted planner plan, with the email model raising `GroqError("groq_invalid_output")`, gives status 503 and `{"detail": "email_model_unavailable"}`.
  - `test_email_cards_need_a_connected_mailbox`: with the mailbox not connected, preparing `email_export` and `email_send` gives 409 `mailbox_not_connected`.
  - `test_attention_on_never_synced_mailbox`: with the last-sync time null, the en text is returned verbatim, and the ms text when the language is `ms`.
- [ ] **Step 2: Run them to verify they fail.** Run each test file through the wrapper. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** per the spec's M1, M2 (email part), M9 and M10.
  - Find the mailbox last-sync column in the mailbox row model; the spec says "the mailbox row's last-sync time is null".
  - Docs: `docs/EMAIL_ASSISTANT.md` gets the 503 and the never-synced answer. `docs/CONSOLE_API.md` gets `email_model_unavailable` and the card-time `mailbox_not_connected`.
- [ ] **Step 4: Run them to verify they pass.** Run the three test files and `tests/test_email_service.py`, each on its own, then `make lint`.
- [ ] **Step 5: Commit** with the message `fix(email): drafting contract, no 500 on model failure, card-time mailbox check, never-synced summary`.

---

### Task 2: HR training prerequisites and the HR chat contract (M2-HR, M4)

**Files:**
- Modify: `csa/hr/assistant.py` (system prompt wrapper line)
- Modify: `csa/hr/service.py` (`save_draft`: `no_employee_link`)
- Modify: `csa/hr/api.py` (`hr_http_error`: `no_employee_link` gives 409)
- Modify: `csa/workspace/executors.py` (`TrainingSubmitExecutor.card`)
- Modify: `csa/workspace/planner.py` (a `PlannerFacts.my_employee` field and the prompt line)
- Modify: `csa/workspace/service.py` (`planner_facts` computes `my_employee` when a training word appears)
- Test: `tests/test_hr_training.py`, `tests/test_workspace_registry.py`, `tests/test_workspace_planner.py`

**Interfaces:**
- Produces:
  - `class EmployeeFact(BaseModel): linked: bool; can_request_training: bool; reason: Literal["hr_inactive","no_hr_access","no_employee_link"] | None`, defined in `planner.py` next to the other facts;
  - `PlannerFacts.my_employee: EmployeeFact | None = None`;
  - the codes 409 `no_employee_link` and 403 `hr_training_not_permitted`.
- The prompt line, verbatim: `- my_employee: present when the message is about training. If linked or can_request_training is false, answer with the reason (training requests need a linked employee record with training access) and do not propose training_submit.`
- The HR chat wrapper line, verbatim: `Return one JSON object: {"action": <one tool call object> or null, "clarification": "request_details" | "manual_decision_required" | "unsupported_request" or null}; exactly one of them is non-null.`

- [ ] **Step 1: Write the failing tests**
  - `test_training_card_requires_employee_link` (docker): a user with no `hr_employee_accounts` row. Preparing or confirming a new `training_submit` gives 409 `no_employee_link`. Count the training request rows before and after: no row is created.
  - `test_training_card_requires_training_grant`: a linked user without the draft or submit grant gets 403 `hr_training_not_permitted`.
  - `test_save_draft_without_link_is_no_employee_link`: from `HrService`.
  - `test_planner_gets_my_employee_on_training_words`:
    - "i want to send a request for ComptiaA+ training" gives `my_employee` present, with `linked=False` and `reason="no_employee_link"`;
    - "Mohon latihan Excel" (ms) gives it present;
    - "show my orders" gives `None`.
  - `test_hr_chat_prompt_states_the_wrapper`: the line is present verbatim.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement** per spec M4 and settled item 2. HR inactive keeps 403 `package_inactive`. `HrPolicyError("permission_denied")` from `context` becomes `hr_training_not_permitted`. `docs/CONSOLE_API.md` gets the two new codes.
- [ ] **Step 4: Run them to verify they pass.** Run the three files and `tests/test_hr_workflows.py`, each on its own, then `make lint`.
- [ ] **Step 5: Commit** with the message `fix(hr): training prerequisites at card time, my_employee fact, HR chat wrapper`.

---

### Task 3: Turn loop: one correction per turn, directory dead end, locked navigation (M5, M7, M8 server)

**Files:**
- Create: `csa/workspace/rules.py`. It holds the pure rules moved out of `service.py`: the placeholder filter, the content check (`_asks_for_content`, `_about_email`), the send rule (`_asked_to_send`'s word logic), and the tab-availability rule for M8. `service.py` imports them, and their behaviour does not change.
- Modify: `csa/workspace/planner.py`. `PlannerInvalidOutput.__init__(self, reason: str | None = None)` stores `self.reason`. The code and status are unchanged.
- Modify: `csa/workspace/service.py`:
  - the `_plan` and `_resolve` loop becomes one helper, `_plan_and_resolve`;
  - `_resolve` raises `PlannerInvalidOutput(reason=...)` at each nameable violation;
  - M5 is in `_finish` / `_resolve`;
  - M8 is in the navigate branch.
- Test: `tests/test_workspace_planner.py`

**Interfaces:**
- Consumes: `_plan` from #56 (`csa/workspace/service.py`).
- Produces:
  - `async def _plan_and_resolve(request, actor, client_id, thread_id, turn, facts, planner, registry) -> tuple[WorkspaceProposal, Planned]`, which replaces the pair of calls in `create_planned_turn`;
  - the reason codes, as string constants in `rules.py`: `recipient_not_requested`, `unknown_thread`, `blank_query`, `option_too_long`, `contract_invalid`;
  - `rules.tab_available(tab: str, role: Role, packages: list[PackageFact]) -> bool`.
- Behaviour (spec settled item 1):
  1. Resolve the first plan.
  2. On a content question, or on a `PlannerInvalidOutput` with a known reason, re-plan once with the matching fixed note. The M7 notes are one sentence per reason, server text.
  3. The second plan is final:
     - a content question gets the content fallback;
     - a known violation gets the M7 fallback answer (`unknown_thread` gets "I couldn't find that conversation in your synced email.", and the others get "I couldn't work out who to send that to. Type their email address."), with Malay versions;
     - an unknown reason stays a 502.
  - **Contract validators:** they fire when `PlannerOutput` is parsed inside `WorkspacePlanner.plan`. Wrap a parse failure there as `PlannerInvalidOutput(reason="contract_invalid")`; this is an additive change to `plan`.

- [ ] **Step 1: Write the failing tests** (docker unless noted)
  - `test_directory_no_match_in_email_asks_for_the_address`: the planner plans `directory_lookup` "danial" for "send email to danial", and the directory is empty.
    - The result is a clarification with the M5 en text verbatim and no options.
    - A typed "danial@example.test" folded answer then composes, and the card is `email_send`.
  - `test_directory_hr_inactive_in_email_asks_for_the_address`: the same, with HR inactive.
  - `test_directory_no_match_outside_email_is_plain`: "who is danial" gives today's answer text, and the ms text for `ms`.
  - `test_contract_violation_gets_one_correction`: plan 1 is `email_compose` to an address the user never wrote; plan 2 is valid.
    - The second plan's draft is made.
    - Exactly 2 calls, and the second carries a `<correction>` block.
  - `test_correction_replan_keeps_recipient_gates`: both plans use unrequested addresses. The fallback answer is given, no draft is made, and there are 2 calls.
  - `test_one_correction_per_turn_across_kinds`: plan 1 asks for content and plan 2 breaks a rule. The M7 fallback is given, with exactly 2 calls.
  - `test_unknown_thread_fallback`: two reply plans with unknown thread ids give the en text verbatim.
  - `test_navigate_to_locked_tab_answers_locked`: a navigate plan to the `email` tab with the `staff_email` package inactive gives the locked answer, not `navigate`.
  - `rules` unit tests (no docker), covering each moved function's existing cases: move or keep the existing tests importing from `rules`.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.** Keep the existing #56 tests passing; a changed expectation needs a reason in the report. `docs/EMAIL_ASSISTANT.md` gets one line on the shared correction budget.
- [ ] **Step 4: Run them to verify they pass.** Run `tests/test_workspace_planner.py`, `tests/test_workspace_contracts.py` and `tests/test_workspace_registry.py`, each on its own, then `make lint`.
- [ ] **Step 5: Commit** with the message `fix(workspace): one correction per turn, directory asks for the address, locked tabs answered`.

---

### Task 4: Console: messages, turns always carry a tab, no contradictory navigation (M3, M6, M8 console)

**Files:**
- Modify: `CSA_WebConsole/csa-console/web/lib/api.ts` (`explained`, and the non-JSON error fallback)
- Modify: `CSA_WebConsole/csa-console/web/lib/email-recipients.ts` (email sentences: `provider_unreachable`, `permission_denied`, `email_model_unavailable`)
- Modify: `CSA_WebConsole/csa-console/web/lib/workspace-api.ts` and/or `components/console/AssistantDock.tsx` (`ask`: the `page_context` fallback; navigate rendering)
- Test: `CSA_WebConsole/csa-console/web/tests/workspace-api.test.mjs`, `tests/email-recipients.test.mjs`, and an api-error test (add one to an existing test file if no api test exists)

**Interfaces:**
- Produces: `export function turnPageContext(context: PageContext | null): PageContext`, which returns `context ?? { tab: "overview" }`. Use the actual `PageContext` type name from `lib/workspace-api.ts` or `lib/page-context.ts`.

- [ ] **Step 1: Write the failing tests**
  - Every code in spec M3 has an `explained` entry: one plain sentence, not equal to the code.
  - The non-JSON 500 message is "Something went wrong on our side. Try again.".
  - `turnPageContext(null)` deep-equals `{tab:"overview"}`, and a given context is returned unchanged.
  - The email sentence map has the three codes.
- [ ] **Step 2: Run** `npm test`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **Sentences:** one per code, saying what happened and what to do.
  - **M4 text:** use the spec's suggested text: "Your login isn't linked to an employee record, so you can't submit training requests. Ask your HR owner to link it."
  - **The dock:**
    - turns always send `turnPageContext(context)`;
    - the navigate result does not print "Opened X." when `tabHref` returns null, and shows only the notice.
- [ ] **Step 4: Run** `npm run lint`, `npm run typecheck` and `npm test`. Expected: all pass. If node is not on PATH, use the wrapper.
- [ ] **Step 5: Commit** with the message `fix(console): every assistant error has a sentence, turns always carry a tab, no contradictory navigate`.

---

### Task 5: Retest checklist and the model smoke run (P1, P2)

**Files:**
- Create: `csa/workspace/smoke_cases.json`
- Create: `scripts/assistant_smoke.py`
- Create: `docs/ASSISTANT_RETEST.md`
- Create: `tests/test_assistant_smoke.py`
- Modify:
  - `deploy/STAGING_PIPELINE.md` ("Routine rollouts": run the checklist, and optionally the smoke run with its command);
  - `docs/PROGRESS.md` (one new first entry for the whole branch);
  - `.claude/skills/staging-rollout/SKILL.md` (after a rollout, offer the smoke run and the checklist).

**Interfaces:**
- Consumes:
  - `csa.workspace.rules` (Task 3);
  - `WorkspacePlanner(client).plan(message, facts, note=None)`;
  - the email drafting entry used by `_compose_with_model` (Task 1). Reach it through `EmailService` built without a pool, or through an extracted pure function, per spec settled item 5.
- Produces: the CLI `python -m scripts.assistant_smoke [--repeat N] [--case ID]`. Exit codes: 0 when all pass, 1 when any fails, 2 on a setup error such as no model key.
- Case schema (JSON list): `{"id": str, "language": "en"|"ms", "message": str, "facts": {"preset": str, ...overrides}, "expect": {"actions": [str], "not_actions": [str]?, "send": bool?, "no_placeholder_options": bool?, "draft_valid": bool?}}`.
  - `preset` names synthetic fact builders in the script: `staff_basic`, `staff_with_hr_linked`, `staff_unlinked`, `staff_with_mailbox`, `staff_hr_inactive`.
  - At least 2 cases per feature, in English and Malay. The features are navigate, answer, directory, compose/send, draft, reply, team, attention, training and the "don't send" case.
- Output per case: `PASS|FAIL <id> action=<action>` plus field names on a failure. Never the message, the model text, subjects or bodies. The script ends with a summary line `N passed, M failed`.

- [ ] **Step 1: Write the failing tests** in `tests/test_assistant_smoke.py`, using a scripted model and no network:
  - `test_smoke_cases_parse_and_cover_features`: the schema is valid; every listed feature has ≥2 cases; both languages are present.
  - `test_every_smoke_case_is_in_the_checklist`: every `id` appears in `docs/ASSISTANT_RETEST.md`.
  - `test_smoke_output_is_content_free_and_exit_code`:
    - scripted answers make one case fail;
    - the exit code is 1;
    - stdout contains no case message text, no scripted subject or body, and no address;
    - the summary line is present.
  - `test_smoke_without_model_key_exits_2`.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement** the script, the cases, the checklist and the docs.
  - **The checklist** has one table per feature, with English and Malay rows: prompt | setup | expected | smoke id or "manual".
  - **It includes these state rows:**
    - no mailbox;
    - no employee link;
    - no directory match;
    - an address in the request;
    - "don't send";
    - an old card's buttons disabled.
  - **The runbook command:** `docker exec csa-staging-gateway-1 python -m scripts.assistant_smoke`.
- [ ] **Step 4: Run them to verify they pass.** Run the file, then `make lint`.
- [ ] **Step 5: Commit** with the message `feat(workspace): retest checklist and owner-triggered model smoke run`.
