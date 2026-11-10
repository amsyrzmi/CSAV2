# Assistant engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One prompt can produce one confirmed `plan` card of several steps (batch email with facts, several bookings), with Jev as the live typed decision layer across the assistant, the inbound-email injection check and the customer classifier.

**Architecture:** A `PlanExecutor` composes the existing executors by kind; each step keeps its own gate, card, version and idempotency key (`uuid5(plan_id, "step:i")`). Email batches expand server-side into `email_send`/`email_export` steps whose drafts are written in parallel from permission-checked facts. One shared `JevClient` feeds a small decider layer (Jev at a floor, LLM or server rule otherwise, sampled audit into the existing `decision_log`).

**Tech Stack:** Python 3.12, FastAPI, psycopg, pydantic, pytest (Docker Postgres), Next.js console, Playwright browser tests, Jev (`csa/decide/jev.py`).

**Spec:** `docs/superpowers/specs/2026-11-09-assistant-engine-design.md` (approved 2026-11-09).

## Global Constraints

- Plan limits: `MAX_STEPS = 20`, `MAX_NON_EMAIL_STEPS = 10`, `PLAN_DEADLINE_SECONDS = 90`, step concurrency 4. Allowed step kinds, nothing else: `email_send`, `email_export`, `booking_action`, `inbox_decision`, `training_submit`, `hr_person`. Never `plan`, never `subscription_action`.
- Send rule: a send card comes only from `rules.asks_to_send` on the user's own words. Neither Jev nor the planner can create one; a decider may only lower send to draft.
- Cards and turn rows hold counts and step titles only: no address, name, subject or body. Recipients and facts used live on the draft (`email_drafts.facts_used`, owner-readable).
- Only the assistant creates or retries a plan (origin `assistant`); a tab cannot (`POST /workspace/proposals` rejects `plan`).
- Jev: `CLASSIFY_TIMEOUT_S = 2.0`, floors 0.7 per `noul` and `choice` question (settings), audit sample 0.10 (setting), only redacted text sent, typed answers only in `decision_log`, never message text. No key means every area runs on the LLM or rule with no error; an explicit `shadow`/`cascade` without a key is a startup refusal.
- Batch mail to customers is service mail to people with a subscription (owner approved sending from the staff member's own Gmail). Over the mailbox's remaining hourly sends (`SEND_LIMIT_PER_HOUR = 20`) the extra steps become `email_export` and the card says so.
- Migration `0542_assistant_plan.sql` (next free after 0541). It stops gateway, frontend, worker, delivery on rollout.
- Repo is public: no real names, addresses or secrets in code, tests, fixtures or docs. Never read `/srv/csa-staging/secrets`.
- Process: CI is the merge gate, so locally run the changed tests and `make lint` (never pipe lint through `tail` inside a `&&` chain). After **any** planner prompt change run the **full** `assistant_smoke`, not only the new cases. Browser tests stub the console API and flag unknown calls: grep every stub for a call you add and run those files by hand. The formatter hook reformats whole files on `Edit`; for test files edit with a script and check `git diff --stat`.

## Review Focus

Failure modes the spec implies; each gets a test in the task that owns the code.
1. The same person named twice, or an address and the employee it belongs to, or a customer with no email: deduplicated and counted, never a second send (Task 6).
2. A customer whose subscription facts the user's role may not read: drafted without them, the card says "no facts used", nothing leaks (Task 7).
3. A Jev answer that is ambiguous or below the floor on shared versus personalised, or says "not a send request": personalised by default, and no decider output ever yields a send card (Task 8).
4. Confirm twice or from a second tab while steps are running, or the lease expiring mid-plan: no step runs twice, no email sent twice (Task 2).
5. A Malay or Manglish prompt with Jev erroring or timing out mid-turn: falls back to the rule or LLM and the turn still completes, not a 500 (Tasks 4, 5).
6. Customer name, plan name or KB text containing instructions ("ignore the above"): reaches the drafter only as data (Task 8).

---

### Task 1: Contracts and migration

**Files:**
- Create: `db/migrations/0542_assistant_plan.sql`
- Modify: `csa/workspace/contracts.py` (add to `ProposalKind`; new models), `csa/workspace/planner.py` (add to `PlannerProposal` union), `docs/console_openapi.json` (regenerate with `scripts/export_console_openapi.py`)
- Test: `tests/test_plan_contracts.py` (new); the test that covers 0531's constraint change (find with `grep -l 0531 tests`) gets the same assertions for 0542.

**Interfaces:**
- Produces in `contracts.py`: `ProposalKind` gains `"plan"`; `PlanStep(kind: Literal[...allowed kinds], payload: dict[str, object], version: str | None)`; `PlanProposalPayload(steps: list[PlanStep] = Field(min_length=1, max_length=20))`; `RecipientSpec(kind: Literal["employee","address","customer","group"], value: str, group: Literal["active_subscribers","renewing_soon"] | None, days: int | None)`; `EmailBatchProposal(action: Literal["email_batch"], recipients: list[RecipientSpec] (1..40), subject: str, instructions: str, language, send: bool = False, offset: int = 0)`; `PlanProposal(action: Literal["plan"], steps: list[PlannerStep])` where `PlannerStep` is the union of `BookingActionProposal`, `TrainingSubmitProposal`, `InboxDecisionProposal`, `EmailBatchProposal`.

- [ ] **Step 1: Write failing tests** in `tests/test_plan_contracts.py`: `test_plan_payload_rejects_plan_and_subscription_action_steps`, `test_plan_payload_rejects_zero_and_twenty_one_steps`, `test_email_batch_requires_a_recipient_and_caps_at_forty`, `test_group_recipient_needs_group_and_renewing_soon_needs_days`, `test_planner_output_accepts_email_batch_and_plan`. Add the migration assertions: `console_proposals_kind_check` allows `plan`, `console_workspace_turns_action_check` allows `plan` and `email_batch`, `email_drafts.facts_used` exists as `jsonb`.
- [ ] **Step 2:** Run `pytest tests/test_plan_contracts.py -q`. Expected: FAIL (names missing).
- [ ] **Step 3:** Add the models as above. Write `0542_assistant_plan.sql` in the style of 0531: drop and recreate both CHECKs from `pg_get_constraintdef` adding the new values, `ALTER TABLE public.email_drafts ADD COLUMN facts_used jsonb`, a rollback comment, no data changes.
- [ ] **Step 4:** Re-run those tests and `pytest tests/test_migrations*.py -q`. Expected: PASS. Regenerate the OpenAPI and check `git diff --stat docs/console_openapi.json` is only the new schemas.
- [ ] **Step 5:** `make lint`, then commit `feat(workspace): plan and email_batch contracts, migration 0542`.

### Task 2: PlanExecutor

**Files:**
- Create: `csa/workspace/plan.py`
- Modify: `csa/workspace/executors.py` (`build_proposal_registry` registers `PlanExecutor` with the other executors as its children), `csa/console/workspace.py` (`create_proposal` rejects kind `plan` with 422 `plan_not_creatable`)
- Test: `tests/test_plan_executor.py` (new, with fake child executors implementing `ProposalExecutor`), plus one route test in `tests/test_console_workspace.py`.

**Interfaces:**
- Produces: `STEP_KINDS: frozenset[str]`; `MAX_STEPS = 20`; `MAX_NON_EMAIL_STEPS = 10`; `PLAN_DEADLINE_SECONDS = 90`; `CONCURRENCY = 4`; `step_id(plan_id: UUID, index: int) -> UUID` (`uuid5(plan_id, f"step:{index}")`); `plan_payload(steps: list[PlanStep]) -> dict[str, Any]`; `class PlanExecutor` with `kind = "plan"`, `__init__(self, children: Mapping[str, ProposalExecutor])`, and the `gate`, `card`, `execute` of the `ProposalExecutor` protocol. Receipt: `{"steps": [{"index": int, "kind": str, "status": "done"|"failed"|"skipped"|"not_run", "code"?: str, "receipt"?: dict}]}`.
- Consumes: `ProposalExecutor`, `CardContent`, `ProposalActionRefused` from `csa/workspace/registry.py`.

- [ ] **Step 1: Write failing tests** in `tests/test_plan_executor.py`: `test_card_lists_one_row_per_step_with_no_addresses`; `test_plan_version_is_hash_of_stored_step_versions`; `test_tab_origin_is_refused`; `test_kinds_outside_the_allowlist_are_refused`; `test_caps_twenty_steps_and_ten_non_email`; `test_execute_runs_steps_in_order_with_derived_ids` (children record `proposal_id` equal to `step_id(plan_id, i)`); `test_a_failing_step_is_recorded_and_the_rest_run`; `test_every_step_failing_with_the_same_refusal_raises_it`; `test_a_drifted_step_is_skipped_not_failed` (child `card` returns a different version, status `skipped`, code `changed`); `test_steps_past_the_deadline_are_not_run` (monkeypatch `PLAN_DEADLINE_SECONDS` to 0.2 and a slow child); `test_replaying_execute_replays_finished_steps` (same ids, children idempotent); `test_concurrent_confirms_run_each_step_once` (the registry-level race with a real `ProposalRegistry`, two confirms, each fake child counts calls, expect one call per step). Route test: `test_post_proposals_rejects_plan_with_422`.
- [ ] **Step 2:** Run `pytest tests/test_plan_executor.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `PlanExecutor`.** `card()`: validate payload with `PlanProposalPayload`, refuse origin `tab` with 403 `plan_assistant_only`, run each distinct step kind's `gate`, call each child's `card` (a child refusal at card time is shown as a "changed" row, not raised), plan version = sha256 of the stored step versions in order. `execute()`: per step, in a bounded `asyncio.Semaphore(CONCURRENCY)` under one `asyncio.timeout(PLAN_DEADLINE_SECONDS)`, re-run the child `card` for its current version and skip on mismatch, then `child.execute(..., proposal_id=step_id(...), expected_version=stored, origin=origin)`; collect `HTTPException` as `failed` with its `detail` as `code`; steps not started at the deadline are `not_run`. Raise only when all steps failed with one identical refusal.
- [ ] **Step 4:** Run the file and `pytest tests/test_workspace_registry.py tests/test_registry.py -q`. Expected: PASS.
- [ ] **Step 5:** `make lint`, commit `feat(workspace): plan executor composing the existing executors`.

### Task 3: Retry route

**Files:**
- Modify: `csa/workspace/plan.py` (`retry_plan`), `csa/console/workspace.py` (route `POST /proposals/{proposal_id}/retry`), `docs/CONSOLE_API.md`
- Test: `tests/test_plan_executor.py` (route-level cases, Docker)

**Interfaces:**
- Produces: `async def retry_plan(request: Request, actor: ConsoleActor, client_id: UUID, proposal_id: UUID, registry: ProposalRegistry) -> ProposalOut`. Consumes `ProposalRegistry.prepare` and `ProposalOut` with `result` receipts from Task 2.

- [ ] **Step 1: Write failing tests:** `test_retry_prepares_a_new_plan_from_failed_and_not_run_steps_only`, `test_retry_is_assistant_origin_and_session_bound` (another session gets 403 `proposal_session_mismatch`), `test_retry_of_a_plan_with_nothing_to_retry_is_409`, `test_retry_of_a_plan_not_yet_executed_is_409`, `test_tab_still_cannot_create_a_plan_after_retry_exists`.
- [ ] **Step 2:** Run them. Expected: FAIL (404 route).
- [ ] **Step 3:** Implement `retry_plan`: lock the plan row as its owner (`_locked`-style read through the registry's `get`), require `status == "executed"`, build steps from the receipt entries with status `failed` or `not_run` using the stored step payloads, call `registry.prepare(..., "plan", payload, origin="assistant")`, return the stored card as `ProposalOut`. Add the route beside confirm and cancel with `Cache-Control: no-store`.
- [ ] **Step 4:** Run the file plus `tests/test_console_workspace.py -q`. Expected: PASS.
- [ ] **Step 5:** `make lint`, commit `feat(workspace): retry the failed or unrun steps of a plan`.

### Task 4: Jev shared setup and the decider layer

**Files:**
- Create: `csa/decide/deciders.py`
- Modify: `csa/core/settings.py` (new settings and validators), `apps/gateway/main.py` (build `app.state.jev_client` once beside the other clients, close it on shutdown), `csa/decide/jev.py` (a `redact_for_jev` seam only if none exists: reuse the redaction that produces `Envelope.message.body_redacted`, `grep body_redacted`)
- Test: `tests/test_assistant_deciders.py` (new), `tests/test_settings*.py` for the validators

**Interfaces:**
- Produces: settings `ASSISTANT_DECIDER_EMAIL`, `ASSISTANT_DECIDER_ROUTING`, `ASSISTANT_DECIDER_INBOUND_INJECTION` each `Literal["auto","llm","shadow","cascade"] = "auto"` (`auto` is `cascade` with a key, else `llm`), `ASSISTANT_JEV_FLOOR_NOUL: float = 0.7`, `ASSISTANT_JEV_FLOOR_CHOICE: float = 0.7`, `ASSISTANT_DECIDER_AUDIT_SAMPLE: float = 0.10`. `class Decided(BaseModel): answers: dict[str, object]; provider: Literal["jev","llm"]; fallback_reason: str | None`. `class Decider` with `__init__(self, area: str, mode: str, client: JevClient | None, pool, *, floors, audit_sample, rng)` and `async def decide(self, state: Mapping[str, object], questions: Mapping[str, dict[str, Any]], *, fallback: Callable[[], Awaitable[Mapping[str, object]]], trace_id: UUID, client_id: UUID) -> Decided`. Writes one `decision_log` row (`node = f"assistant_{area}"`) when mode is `shadow`/`cascade`.

- [ ] **Step 1: Write failing tests:** `test_llm_mode_never_calls_jev`; `test_cascade_uses_jev_at_or_above_the_floor_and_skips_the_fallback`; `test_cascade_below_floor_uses_the_fallback_and_logs_reason`; `test_jev_error_or_timeout_falls_back_without_raising` (Review Focus 5); `test_shadow_uses_the_fallback_and_logs_both_answers_and_disagreement`; `test_audit_sample_runs_the_fallback_beside_jev_and_logs_a_disagreement`; `test_decision_log_rows_carry_no_message_text`; `test_only_redacted_text_reaches_jev` (a fake client records its `state`; an address and a name in the input are redacted); `test_auto_with_no_key_is_llm_and_never_errors`; settings: `test_explicit_cascade_without_a_key_refuses_startup`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `Decider` and the settings validators; construct the `JevClient` in `apps/gateway/main.py` only when `TYPESAFE_API_KEY` is set, sharing one `httpx.AsyncClient`; `redact_for_jev(text) -> str` reuses the existing redaction. Insert logs with `db.q` through the tenant transaction used by the customer path's logger (`csa/decide/providers.py` `DecisionRecord`).
- [ ] **Step 4:** Run the new file, `tests/test_decide_providers.py`, the settings tests. Expected: PASS.
- [ ] **Step 5:** `make lint`, commit `feat(decide): shared Jev client and the assistant decider layer`.

### Task 5: Routing decider in the planner facts

**Files:**
- Create: `csa/workspace/routing.py`
- Modify: `csa/workspace/service.py` (`planner_facts`: replace the five `_X_WORDS.search(turn.body)` tests with the routing result)
- Test: `tests/test_workspace_routing.py` (new), `tests/test_workspace_planner.py` (existing fact cases must still pass unchanged)

**Interfaces:**
- Produces: `FACT_GROUPS = ("manager","mail","team","training","people")`; `@dataclass(frozen=True) class Needs: manager: bool; mail: bool; team: bool; training: bool; people: bool`; `async def route_facts(decider: Decider | None, body: str, *, trace_id: UUID, client_id: UUID) -> Needs`. The fallback is the existing regexes (`_MANAGER_WORDS`, `_MAIL_WORDS`, `_TEAM_WORDS`, `_TRAINING_WORDS`, `_PEOPLE_WORDS`), which stay in `service.py` and are passed in or imported.

- [ ] **Step 1: Write failing tests:** `test_jev_yes_for_people_reads_people_access_without_a_trigger_word`; `test_below_floor_uses_the_word_rules`; `test_jev_error_uses_the_word_rules` (Review Focus 5, with a Malay and a Manglish message); `test_no_decider_is_exactly_todays_behaviour` (the whole existing `planner_facts` test set passes); `test_routing_questions_are_noul_per_group`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `route_facts`: one Jev request with `noul` questions named `needs:<group>` ("Does `message` need <group description>?"), result `Needs` where each group is Jev at/above floor else its regex. In `planner_facts` build `Needs` once and use it for the five reads; `booking_facts` stays unconditional. The decider is `app.state.routing_decider` (None when the area is `llm`).
- [ ] **Step 4:** Run the new file and `pytest tests/test_workspace_planner.py -q`. Expected: PASS.
- [ ] **Step 5:** `make lint`, commit `feat(workspace): route planner facts with the decider, word rules as fallback`.

### Task 6: Recipients, groups and quota

**Files:**
- Create: `csa/workspace/email_batch.py` (this task: recipients only), `db/sql/subscriptions/customers_by_group.sql`
- Modify: `csa/email/service.py` (`async def remaining_sends(self, session: EmailSession) -> int`, a public wrapper over `_recent_sends`)
- Test: `tests/test_email_batch_recipients.py` (new, Docker)

**Interfaces:**
- Produces: `@dataclass(frozen=True) class BatchRecipient: key: str; employee_id: UUID | None; address: str; customer_id: UUID | None; label: str` (`label` is for the assistant's reply only, never stored on a card); `@dataclass class Expanded: recipients: list[BatchRecipient]; not_included: dict[str, int]` (reason to count); `async def expand_recipients(request, actor, client_id, specs: list[RecipientSpec], *, offset: int, limit: int) -> Expanded`. Reasons: `no_email`, `not_found`, `ambiguous`, `duplicate`, `over_limit`. Consumes `search_customers`, the directory resolver in `csa/workspace/directory.py`, `require_package`.

- [ ] **Step 1: Write failing tests:** `test_named_people_resolve_and_duplicates_collapse` (same person twice, and an address plus the employee it belongs to, Review Focus 1); `test_customer_without_email_is_not_included_with_reason`; `test_active_subscribers_group_only_returns_active_with_email`; `test_renewing_soon_group_respects_days`; `test_group_larger_than_limit_returns_first_page_and_offset_gives_next`; `test_ambiguous_name_is_not_included_and_counted`; `test_group_needs_subscriptions_package_and_role` (403 refusal, no rows); `test_remaining_sends_counts_the_hourly_window`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `expand_recipients` (deterministic order by id for groups, `offset`/`limit` page) and `remaining_sends`. `customers_by_group.sql` takes `client_id`, `group`, `days`, `limit`, `offset` and returns customers with a non-null email whose subscription matches (`active`; or `renews_at` within `days`).
- [ ] **Step 4:** Run the file and `pytest tests/test_email_service.py -q`. Expected: PASS.
- [ ] **Step 5:** `make lint`, commit `feat(email): batch recipients, subscriber groups and send quota`.

### Task 7: Facts for drafts

**Files:**
- Create: `csa/workspace/draft_facts.py`, `db/sql/kb/search_chunks_readonly.sql` only if `search_chunks.sql` cannot be reused unchanged (reuse it; it has no side effect, the gap record is in `KbAgent._query`)
- Test: `tests/test_draft_facts.py` (new, Docker)

**Interfaces:**
- Produces: `@dataclass(frozen=True) class DraftFact: id: str; text: str; source: str` (`subscription:<id>` or `kb:<title>`); `async def subscription_facts(request, actor, client_id, customer_id: UUID) -> list[DraftFact]` (plan name, status, renewal date, price; empty when the user's role or the package does not allow Subscriptions); `async def kb_facts(request, client_id, topic: str, *, limit: int = 3, threshold: float = 0.35) -> list[DraftFact]` using `app.state` embedder and `kb/search_chunks.sql`, never `KbAgent._query`, never `kb/record_gap.sql`.

- [ ] **Step 1: Write failing tests:** `test_subscription_facts_for_a_customer_with_a_plan`; `test_role_without_subscriptions_read_gets_no_facts_and_no_error` (Review Focus 2); `test_package_off_gets_no_facts`; `test_kb_facts_returns_matches_over_threshold`; `test_kb_search_never_records_a_gap` (no row in `kb_gaps` after a miss); `test_fact_text_is_plain_data_with_ids`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement both functions through the existing services, with the same role and package checks the Subscriptions tab applies (reuse `require_package` and the role rule used by `csa/subscriptions/api.py`).
- [ ] **Step 4:** Run the file. Expected: PASS.
- [ ] **Step 5:** `make lint`, commit `feat(workspace): permission-checked subscription and KB facts for drafts`.

### Task 8: Batch drafting, checks, email decider and step assembly

**Files:**
- Modify: `csa/workspace/email_batch.py` (drafting and assembly), `csa/email/service.py` (`_compose` accepts `facts: Sequence[DraftFact] = ()` and stores `facts_used`; the draft prompt gets a `facts` list marked as data), `csa/core/settings.py` (`ASSISTANT_DRAFT_MODEL: str | None = None`), `apps/gateway/main.py` (a separate model client for assistant batch drafting only, default the same model), `csa/decide/grounding.py` only to expose a pure `unsupported_figures(text, facts)` if `ungrounded` cannot be used as is
- Test: `tests/test_email_batch.py` (new, Docker, scripted model)

**Interfaces:**
- Produces: `EmailDecision(mode: Literal["shared","personalised"], sources: set[Literal["subscription","kb"]])`; `async def decide_email(decider, state, *, trace_id, client_id) -> EmailDecision` (fallback: the planner's own fields, default `personalised`, sources from the recipients and topic); `async def build_batch_steps(request, actor, client_id, thread_id, turn, proposal: EmailBatchProposal, expanded: Expanded, registry, decision: EmailDecision) -> BatchSteps` with `BatchSteps(steps: list[PlanStep], not_included: dict[str, int], notes: list[str])`. Each step is `email_send` (draft clean and the send allowed by the send rule and quota) or `email_export` (placeholder left, over quota, or the user did not ask to send). Consumes Tasks 4, 6, 7 and `rules.asks_to_send` on `turn.body`.

- [ ] **Step 1: Write failing tests:** `test_shared_mode_makes_one_draft_body_sent_as_separate_messages`; `test_personalised_mode_makes_one_draft_per_recipient_in_parallel` (assert at most 4 concurrent model calls); `test_a_draft_with_a_placeholder_becomes_an_export_step`; `test_over_quota_steps_become_export_steps_and_notes_say_how_many`; `test_decider_can_never_create_a_send_step` (Jev "send: yes" with a user message that has no send word yields `email_export`, Review Focus 3); `test_low_confidence_mode_defaults_to_personalised` (Review Focus 3); `test_figures_not_in_the_facts_become_placeholders`; `test_review_pass_runs_only_for_drafts_that_used_facts`; `test_facts_in_drafter_prompt_are_marked_untrusted_data_and_injection_text_is_inert` (Review Focus 6); `test_facts_used_are_stored_on_the_draft_not_on_the_card`; `test_card_and_turn_rows_contain_no_address_or_name`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement the module. Per recipient (semaphore 4): gather facts for the decided sources, call the extended `_compose` through `EmailService`, run the figure/link check, run a model review for fact-using drafts (reuse the customer path's verifier in `csa/decide/llm.py` if its signature fits, otherwise a small `review_draft` in this module), then `_draft_card`-equivalent preparation returning a `PlanStep` with its stored `version`. Quota: `remaining_sends` decides how many steps may be `email_send`.
- [ ] **Step 4:** Run the file, `tests/test_email_service.py`, `tests/test_workspace_planner.py`. Expected: PASS.
- [ ] **Step 5:** `make lint`, commit `feat(workspace): batch email drafting with facts, checks and the email decider`.

### Task 9: Planner actions, prompt, resolution and smoke cases

**Files:**
- Modify: `csa/workspace/planner.py` (`SYSTEM_PROMPT` gains one short section for `email_batch` and `plan`), `csa/workspace/service.py` (`_resolve`: `EmailBatchProposal` expands via Tasks 6 to 8 and returns a `_Planned` with a prepared `plan`; `PlanProposal` resolves each step through the existing resolvers, leaving unresolved ones counted), `csa/workspace/smoke_cases.json`, `docs/ASSISTANT_RETEST.md`
- Test: `tests/test_workspace_planner.py` (pins and turn-level cases), `tests/test_assistant_smoke.py`

**Interfaces:**
- Consumes: `build_batch_steps`, `plan_payload`, `PlanExecutor` registration, `_Planned.prepared`. Produces the turn result `proposal` card of kind `plan` that `ProposalCard` renders in Task 11.

- [ ] **Step 1: Write failing tests:** prompt pins (`email_batch` and `plan` described, the can_change gate and "never ask your own booking questions" still present, no bullet duplicating an existing rule: the lesson of #86); `test_email_batch_turn_creates_a_plan_card_of_email_steps` (scripted planner reply); `test_plan_of_two_bookings_resolves_each_and_prepares_one_plan`; `test_unresolvable_step_is_left_off_and_counted`; `test_plan_over_the_cap_is_refused_with_the_split_message`; `test_batch_without_the_email_package_is_refused_before_any_draft`; smoke-case schema test for the new cases.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Add the prompt section (short, no repeated rules), the two resolver branches, and smoke cases `email_batch-en-1/-ms-1` (several people), `email_batch_group-en-1`, `plan_bookings-en-1/-ms-1`, each with `expect.actions` and fields.
- [ ] **Step 4:** Run the changed test files, then `make lint`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(workspace): email_batch and plan planner actions`. Note in the commit body that the full smoke must run on staging after rollout (it cannot run here).

### Task 10: Inbound injection check before reply drafting

**Files:**
- Modify: `csa/workspace/service.py` (`_reply`), `csa/email/service.py` (a guard hook before `_draft_with_model` for replies), `csa/decide/deciders.py` (the area helper)
- Test: `tests/test_email_reply_injection.py` (new, Docker)

**Interfaces:**
- Produces: `async def inbound_injection(decider, thread_body: str, *, trace_id, client_id) -> bool` (Jev `noul` question "injection" at or above floor means True; the fallback is `False` because the drafter's own untrusted-data instruction stays). Refusal: 422 `inbound_email_blocked` with a plain reason; no draft row, no model call to the drafter.

- [ ] **Step 1: Write failing tests:** `test_jev_injection_yes_blocks_the_reply_and_makes_no_draft`; `test_jev_no_still_drafts_with_the_untrusted_data_rule`; `test_below_floor_or_error_drafts_normally`; `test_the_llm_cannot_override_a_jev_yes`; `test_injection_disagreement_with_the_audit_is_always_logged`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement the check in `_reply` before drafting, using `app.state.inbound_decider` (None when the area is `llm`).
- [ ] **Step 4:** Run the file and `pytest tests/test_email_service.py tests/test_workspace_planner.py -q`. Expected: PASS.
- [ ] **Step 5:** `make lint`, commit `feat(workspace): block drafting a reply to an email that tries to steer the assistant`.

### Task 11: Plan card in the console

**Files:**
- Modify: `CSA_WebConsole/csa-console/web/lib/workspace-api.ts` (`ProposalKind` gains `"plan"`, `retryProposal(id)`), `components/console/ProposalCard.tsx` (plan rows, per-step result badges, retry action), `lib/assistant-copy.ts` (English and Malay text), every browser test that stubs the proposal calls
- Test: `CSA_WebConsole/csa-console/web/tests/plan-card.test.mjs` (new), `tests/test_assistant_dock_browser.py` (or the existing dock browser file; find with `grep -l AssistantDock tests/*.py`) gains a plan case

**Interfaces:**
- Consumes: `ProposalOut` of kind `plan` (`card.fields` one row per step, `result.steps[]` after execution) and `POST /workspace/proposals/{id}/retry`.

- [ ] **Step 1: Write failing tests:** library test `retryProposal` posts to the retry route with the CSRF header; browser cases `test_plan_card_shows_one_row_per_step_and_confirms_once`, `test_executed_plan_shows_a_badge_per_step`, `test_retry_button_appears_only_for_failed_or_not_run_steps_and_opens_a_new_card`, `test_email_step_opens_its_draft_through_the_existing_preview`, `test_malay_plan_card_text`.
- [ ] **Step 2:** Run the node test and the browser case. Expected: FAIL.
- [ ] **Step 3:** Implement. Reuse `EmailPreview` per email step; no layout change in `AssistantDock`.
- [ ] **Step 4:** Run the new tests, then grep every browser stub of the workspace proposal routes (`grep -ln "workspace/proposals" tests/*.py`) and run those files by hand; add the new route to each stub that needs it. Expected: all PASS.
- [ ] **Step 5:** `make lint` and the web lint script, commit `feat(web): plan card with per-step results and retry`.

### Task 12: Customer classifier wiring, evaluation gate, docs and rollout notes

**Files:**
- Create: `scripts/assistant_jev_eval.py`, `docs/superpowers/evals/assistant_jev_labels.json` (synthetic labelled messages, English and Malay)
- Modify: the class at `csa/turn/deps.py:221` construction site (pass `app.state.jev_client`'s `JevProvider`), `csa/graph/policy/gate.py` default only through the settings path described below, `deploy/staging-rollout.env` is **not** touched here (a flip is its own PR), `docs/CONSOLE_API.md`, `docs/console_openapi.json`, `docs/RUNBOOK.md` (Jev switches, kill switch, audit queries, secret file), `docs/ASSISTANT_RETEST.md`, `docs/PROGRESS.md`
- Test: `tests/test_assistant_jev_eval.py` (new), `tests/test_decide_providers.py` (customer wiring)

**Interfaces:**
- Produces: `python -m scripts.assistant_jev_eval` prints, per area and question, agreement with the labels and whether it meets the floor, content-free; exit 1 when a question is below its target. Customer path: `decide_mode` default stays `llm` in stored tenant config, and the new default `cascade` applies only when the Jev client exists and the tenant has no explicit mode (settings key `CUSTOMER_DECIDE_MODE_DEFAULT: Literal["llm","cascade"] = "llm"`, owner flips it last).

- [ ] **Step 1: Write failing tests:** eval script reads the labels, calls a fake Jev client and reports agreement; `test_customer_turn_uses_jev_when_cascade_is_default_and_a_client_exists`; `test_tenant_explicit_llm_mode_wins`; `test_no_client_means_llm_whatever_the_default`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement the script and the wiring; write the docs (rollout order: images and migration 0542 with no key wired into the services, so every area runs on the LLM or rule; the owner runs `assistant_jev_eval` with the key passed from their own shell, not a repo file; only after it passes the owner places the `TYPESAFE_API_KEY` secret file, which turns the staff areas on as `auto` becomes `cascade`; then the owner flips `CUSTOMER_DECIDE_MODE_DEFAULT` to `cascade` last, after checking the audit rows); regenerate OpenAPI.
- [ ] **Step 4:** Run the changed tests and `make lint`. Then run the whole planned set once: `pytest tests/test_plan_contracts.py tests/test_plan_executor.py tests/test_assistant_deciders.py tests/test_workspace_routing.py tests/test_email_batch_recipients.py tests/test_draft_facts.py tests/test_email_batch.py tests/test_email_reply_injection.py tests/test_assistant_jev_eval.py -q`. Expected: PASS.
- [ ] **Step 5:** Commit `docs(assistant): Jev switches, evaluation gate and rollout order`. Then the whole-branch review (fresh reviewer, strongest model) and one fix pass, as the execution skill says. Do not push or open the PR until the owner says so.
