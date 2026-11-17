# Inbox refinement, part 1a: the queue (backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every customer conversation carries a queue state, priority, due time and optional claim that the database keeps correct; the Inbox API lists, filters, sorts, counts and acts on it quickly at 50,000 conversations per company.

**Architecture:** Queue columns on `public.conversations`; one plpgsql function `inbox_queue_enter` used by two triggers (inbound customer message, escalation insert) and by Python; every other transition is Python plus SQL in a new `csa/inbox_queue` package called by the code that knows the actor; new `/inbox` routes expose list, counts, assignees, settings, queue actions, bulk actions and saved views. Backend only: the UI is plan 1b.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 async, PostgreSQL (plpgsql, partial and trigram indexes), pytest with Docker Postgres, Pydantic.

**Spec:** `docs/superpowers/specs/2026-11-16-inbox-refinement-design.md` (sections 4 to 6 and 9, PR 1). Plan 1b (UI), PR 2 (reminders and notification centre) and PR 3 (targets page, email) follow in their own plans. `W` means `CSA_WebConsole/csa-console/web` (not touched by this plan).

## Global Constraints

- Public repo: synthetic data only (invented names, `example.test` addresses, `+60 12-555 xxxx`-style numbers that are not real). Never read anything under `/srv/csa-staging/secrets`. No staging rollout, deploy, home-server action or GitHub setting change; stop at "PR open, CI green" and tell the owner.
- Migrations: **0620, 0621, 0622 only** (range 0620 to 0629 is this project's). 0620 queue columns, indexes and backfill function; 0621 `inbox_settings`, `inbox_queue_events`, `inbox_saved_views`; 0622 `inbox_queue_enter` and the two triggers. Additive, forward-only, no `CREATE INDEX CONCURRENTLY`, constraints on populated tables `NOT VALID` then `VALIDATE`. Raise `EXPECTED_SCHEMA_VERSION` (`apps/gateway/main.py`) to `"0622"`; tests import it and never hardcode it. Add the three migrations to `db/migrations/README.md` (new bullet for 0620 to 0629).
- Every new table with `client_id`: `ENABLE` (not `FORCE`) row security, one policy named `tenant_isolation` using `nullif(current_setting('app.client_id', true), '')::uuid` in `USING` and `WITH CHECK`, explicit grants for `csa_app` and `csa_ro`, and a test that it returns nothing without the tenant setting and nothing for another company (pattern: `tests/test_media_schema.py`).
- Keep SQL in `db/sql/`, parameterized; psycopg 3 async only; Pydantic contracts; surface errors, no silent fallbacks.
- `uv run pyright` runs on the whole project including tests; also `ruff check`, `ruff format --check` and `lint-imports`. Run pyright, ruff and format check on every changed Python file.
- A PostToolUse formatter mass-reformats after the Edit tool: change files with Write or small python replace scripts and check `git diff --stat`. Worktree sessions refuse `VAR=value` prefixes and compound commands: use the wrapper `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/31dc140d-0929-4994-9647-6e6e38e51833/scratchpad/ci.sh <cmd>` (`python -m pytest`, `pyright`, `ruff`, `lint-imports`). Host memory is tight: run test files **one at a time**, check `free -m` first, never the whole suite (CI runs it). A hook blocks rebase and force-push: merge main, never rebase.
- Customer-facing behaviour must not change: no edit to prompts, replies, escalation rules or what the bot says. The PR says the golden and eval harness is not triggered.
- Staff-action receipts (`console_inbox_actions.result`) persist `InboxConversation`: new fields on that model carry ids and enums only, never names or text.
- `docs/console_openapi.json` is regenerated with `scripts/export_console_openapi.py`; `docs/CONSOLE_API.md` documents every new route and parameter; `docs/PROGRESS.md` gets a short entry.
- Commit trailers: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_012Xj1GQFBtX7jxjqQanM4hp`. PR via `gh api -X POST repos/amsyrzmi/CSAV2/pulls --input <json>` (`gh pr edit` fails); body ends with the "Generated with Claude Code" line and the session URL.
- Ledger: `.superpowers/sdd/2026-11-16-inbox-refinement-1a-queue-backend/progress.md` (git-ignored).

## Review Focus

1. **Hot path and the 409 trap.** The inbound trigger adds one `UPDATE` per message and never bumps `inbox_version` (a bump per customer message would make every staff reply fail with 409); a replayed inbound insert (`ON CONFLICT DO NOTHING`) changes nothing. (Task 3)
2. **Not a staff answer.** Automated assistant messages (payment notices, booking messages, bot replies, a rejected or expired held reply's follow-up, the staff-chat order decisions) never move an item to waiting on customer or claim it; only a person's reply and an approved or edited held reply do. (Tasks 3, 4)
3. **No state that disagrees with the conversation.** Sandbox (`web`) and console chat conversations never leave `bot`; a `needs_staff` item always has a reason, `needs_staff_since` and `due_at`; resolve is refused while a held reply is pending and when the customer wrote since the person loaded the item; a hand back leaves no open escalation or ticket. (Tasks 1, 3, 5, 7)
4. **Scale.** At 50,000 conversations for one company every tab and the counts use their partial index with no sort node; the `sender` filter and search stay bounded. (Task 9)
5. **Isolation and roles.** Another company's conversation is 404 on every new route; read-only is 403 on every Inbox route; tenant staff cannot assign to another member, admins can; every new table passes the row security tests. (Tasks 2, 6, 7, 8)

---

### Task 1: Migration 0620, queue columns, indexes, backfill

**Files:**
- Create: `db/migrations/0620_inbox_queue_columns.sql`, `tests/test_inbox_queue_schema.py`
- Modify: `apps/gateway/main.py` (`EXPECTED_SCHEMA_VERSION` to `"0620"` for now; Task 3 raises it to `"0622"`), `db/migrations/README.md`

**Interfaces:**
- Produces: on `public.conversations` the columns `queue_status text NOT NULL DEFAULT 'bot'` (check: `bot`, `needs_staff`, `waiting_customer`, `snoozed`, `resolved`), `queue_reason text` (`escalation`, `held_reply`, `customer_message`, `manual`), `topic text` (`purchase`, `question`, `complaint`, `payment`, `booking`, `subscription`, `other`), `priority text NOT NULL DEFAULT 'normal'` (`low`, `normal`, `high`, `urgent`), `assignee_user_id uuid` (references `console_users(id)`), `claimed_at`, `needs_staff_since`, `due_at`, `snoozed_until`, `resolved_at` (all `timestamptz`), `last_activity_at timestamptz NOT NULL DEFAULT now()`, `last_customer_at timestamptz`, `last_staff_reply_at timestamptz`. A check that `queue_status = 'needs_staff'` implies `queue_reason`, `needs_staff_since` and `due_at` are set (added `NOT VALID`, then validated). Partial and ordinary indexes from design 6.5: `(client_id, last_activity_at DESC, id DESC)`; `(client_id, due_at, id) WHERE queue_status = 'needs_staff'`; `(client_id, assignee_user_id, due_at, id) WHERE queue_status IN ('needs_staff','waiting_customer') AND assignee_user_id IS NOT NULL`; `(client_id, needs_staff_since, id) WHERE queue_status = 'needs_staff'`; `(client_id, snoozed_until) WHERE queue_status = 'snoozed'`; `(client_id, last_staff_reply_at) WHERE queue_status = 'waiting_customer'`; GIN trigram indexes (`pg_trgm`, already installed) on `contacts.full_name`, `contacts.telegram_username` and `contacts.phone_digits`. Function `public.inbox_queue_backfill(p_client uuid DEFAULT NULL) RETURNS integer`: sets the new columns from existing data for conversations still in the default queue state, idempotent, called once by the migration for all companies. Rules: `last_activity_at` and `last_customer_at` and `last_staff_reply_at` from the newest message (`last_staff_reply_at` from the newest `outbound` message with `author = 'human_support'` or `send_authority = 'reviewed'`); conversations that are open escalated, or human-owned whose newest message is inbound, or hold a live pending approval, and whose triggering event (open escalation `created_at`, pending approval `created_at`, newest customer message) is within 30 days, become `needs_staff` with `needs_staff_since` set to that event time, reason `escalation` / `held_reply` / `customer_message` in that precedence, priority from the open escalation (else `normal`), `due_at = needs_staff_since + 240 minutes` placeholder (Task 2's settings do not exist yet; the migration uses the `normal` default of 240 for every priority, and PR 2's rollout job gives them a fresh clock); human-owned conversations whose newest message is from staff become `waiting_customer`; sandbox (`channel` not `telegram`/`whatsapp`) and console chat conversations (`console_chat_threads`, `console_chat_participants`, as `db/sql/inbox/list_conversations.sql` excludes them) stay `bot`; everything else stays `bot`.
- Consumes: nothing from earlier tasks.

- [ ] **Step 1: Write failing tests** in `tests/test_inbox_queue_schema.py` (pattern `tests/test_assistant_page_keys_schema.py` and `tests/test_media_schema.py`, `requires_docker`): the column checks refuse bad `queue_status`, `queue_reason`, `topic`, `priority` and a `needs_staff` row without reason, `needs_staff_since` or `due_at`, and accept the edge values; every index above exists with its predicate (read `pg_indexes`); the `assignee_user_id` foreign key refuses an unknown user; running `0620` again changes nothing; backfill cases, each seeded with synthetic conversations and messages then `SELECT public.inbox_queue_backfill()`: an escalated open conversation (priority from its escalation) is `needs_staff` reason `escalation`; a human-owned conversation whose newest message is inbound is `needs_staff` reason `customer_message`; one with a live pending approval is `needs_staff` reason `held_reply`; one whose newest message is a human reply is `waiting_customer` with `last_staff_reply_at` set; an escalated conversation whose escalation is 31 days old stays `bot`; a `web` channel conversation and a console chat conversation stay `bot`; an ordinary bot conversation stays `bot` with `last_activity_at` equal to its newest message time; a second call returns 0 and changes nothing.
- [ ] **Step 2: Run** `bash <wrapper> python -m pytest tests/test_inbox_queue_schema.py -q`; expect failures (columns missing).
- [ ] **Step 3: Implement** the migration (columns added, then backfill function, then `SELECT public.inbox_queue_backfill()`, then `VALIDATE CONSTRAINT`, then indexes), a comment block with the rollback note as earlier migrations have, the `EXPECTED_SCHEMA_VERSION` bump and the README bullet.
- [ ] **Step 4: Run** the schema test file and `tests/test_media_schema.py::test_max_applied_version_is_the_expected_one`; pyright and ruff on the test file; expect pass.
- [ ] **Step 5: Commit** `feat(inbox): queue columns, indexes and backfill`.

### Task 2: Migration 0621, settings, events, saved views

**Files:**
- Create: `db/migrations/0621_inbox_queue_tables.sql`, `tests/test_inbox_queue_tables_schema.py`
- Modify: `apps/gateway/main.py` (`"0621"`), `db/migrations/README.md`

**Interfaces:**
- Produces three tenant tables.
  - `inbox_settings`: `client_id uuid PRIMARY KEY REFERENCES clients(id)`, `version integer NOT NULL DEFAULT 0`, `target_urgent_min` default 15, `target_high_min` 60, `target_normal_min` 240, `target_low_min` 1440 (each 5 to 10080), `repeat_minutes` 60 (15 to 1440), `max_repeats` 3 (0 to 10), `follow_up_hours` 48 (0 or 1 to 720), `stale_claim_hours` 8 (0 or 1 to 168), `handback_prompt_hours` 24 (0 or 1 to 720), `updated_at`. Grants: `csa_app` select, insert, update; `csa_ro` select.
  - `inbox_queue_events`: `id bigserial`, `client_id`, `conversation_id`, `actor_user_id uuid NULL`, `action text` (check list: `entered`, `replied`, `claimed`, `unclaimed`, `assigned`, `snoozed`, `woke`, `resolved`, `handed_back`, `priority_set`, `topic_set`, `reason_changed`), `from_status text`, `to_status text`, `details jsonb`, `created_at`; index `(client_id, conversation_id, id DESC)`. Append-only: `csa_app` select and insert only; `csa_ro` select.
  - `inbox_saved_views`: `id uuid`, `client_id`, `user_id uuid REFERENCES console_users(id)`, `name text` (1 to 60 chars), `filters jsonb NOT NULL`, `created_at`; unique `(client_id, user_id, lower(name))`. `csa_app` select, insert, update, delete.
- Consumes: Task 1 columns (no code dependency).

- [ ] **Step 1: Write failing tests** in `tests/test_inbox_queue_tables_schema.py`: per table the row security test (nothing without the tenant setting, nothing for another company, one policy named `tenant_isolation` with `app.client_id` in qual and with-check), the grants (events: `csa_app` cannot update or delete; settings and saved views as listed), check constraints refuse and accept edges (each range, each event action, name length, duplicate view name differing only by case), the foreign keys, and that a company with no settings row reads nothing (defaults are the API's job, Task 6).
- [ ] **Step 2: Run** the file; expect failures.
- [ ] **Step 3: Implement** the migration and bookkeeping (`EXPECTED_SCHEMA_VERSION` `"0621"`, README).
- [ ] **Step 4: Run** the file; pyright and ruff; expect pass.
- [ ] **Step 5: Commit** `feat(inbox): settings, queue events and saved views tables`.

### Task 3: Migration 0622, `inbox_queue_enter` and the two triggers

**Files:**
- Create: `db/migrations/0622_inbox_queue_triggers.sql`, `tests/test_inbox_queue_triggers.py`
- Modify: `apps/gateway/main.py` (`"0622"`), `db/migrations/README.md`

**Interfaces:**
- Produces `public.inbox_queue_enter(p_client uuid, p_conversation uuid, p_reason text, p_priority text DEFAULT 'normal', p_actor uuid DEFAULT NULL, p_claim_for uuid DEFAULT NULL) RETURNS boolean`. It is a no-op returning false for a conversation whose channel is not `telegram` or `whatsapp` or that is a console chat conversation. Otherwise: if the item is not `needs_staff`, it becomes `needs_staff` with `needs_staff_since = now()`, `queue_reason = p_reason`, `priority = p_priority`, `resolved_at`/`snoozed_until` cleared; if it already is `needs_staff`, `needs_staff_since` is kept, the reason moves only upward in the precedence `escalation` > `held_reply` > `manual` > `customer_message`, and the priority only upward. Either way `due_at = needs_staff_since + (the company's target minutes for the resulting priority, read from inbox_settings with the defaults of Task 2 when no row exists) * interval '1 minute'`; `p_claim_for` sets `assignee_user_id` and `claimed_at` when the item is unclaimed; it writes one `inbox_queue_events` row (`entered`, or `reason_changed` when only the reason or priority changed) and does **not** change `inbox_version`. Returns true when the row changed.
- Trigger `inbox_queue_after_message` `AFTER INSERT ON public.messages FOR EACH ROW`: updates `conversations.last_activity_at = greatest(last_activity_at, NEW.created_at)` for every message; for `direction = 'inbound'` and `author = 'contact'` also `last_customer_at`, and the inbound transition: when `queue_status IN ('waiting_customer','snoozed','resolved')` the item becomes `needs_staff` via `inbox_queue_enter(…, 'customer_message', priority)` if `owned_by = 'human_support'` or a live pending approval exists, else `bot` (clearing assignee, `snoozed_until`, `resolved_at`, due and reason); an item already `needs_staff` or `bot` is unchanged. It never touches `inbox_version` and never reacts to outbound messages. It does nothing for conversations excluded as above.
- Trigger `inbox_queue_after_escalation` `AFTER INSERT ON public.escalations FOR EACH ROW WHEN (NEW.conversation_id IS NOT NULL)`: calls `inbox_queue_enter(NEW.client_id, NEW.conversation_id, 'escalation', NEW.priority)`. (Three writers insert escalations: the escalation agent, `turn/deps.py` fail-safe and the approval-expiry SQL; a trigger covers all.) An insert skipped by `ON CONFLICT DO NOTHING` does not fire it.
- Consumes: Tasks 1 and 2.

- [ ] **Step 1: Write failing tests** in `tests/test_inbox_queue_triggers.py` using real inserts on synthetic data (use `db/sql/worker/insert_message.sql` and `turn/insert_escalation.sql` through the repo's query loader, as `tests/test_console_inbox.py` does): inbound customer message sets `last_activity_at` and `last_customer_at`; inbound on a `waiting_customer` item with `owned_by='human_support'` makes `needs_staff` reason `customer_message` with `due_at` = since + 240 min and one event row; the same with `owned_by='ai'` and no approval returns the item to `bot`; with a live pending approval and `owned_by='ai'` it is `needs_staff`; inbound on an already `needs_staff` item keeps reason and `needs_staff_since`; **outbound** messages (assistant, `human_support`, `send_authority='reviewed'`) change `last_activity_at` only and never the queue state (Review Focus 2); `inbox_version` is identical before and after an inbound message and after an escalation (Review Focus 1); a replayed inbound insert (same idempotency key) changes nothing; an escalation insert makes `needs_staff` reason `escalation` with the escalation's priority and `due_at` from the company's target (test once with no settings row and once with a row setting `target_high_min = 30`); a second escalation of higher priority raises priority and recomputes `due_at` from the same `needs_staff_since`; a lower priority changes nothing; a `held_reply` item receiving an escalation becomes reason `escalation`; sandbox (`web` channel) and console chat conversations stay `bot` through all of this; `inbox_queue_enter(…, p_claim_for)` claims an unclaimed item and leaves an existing claim; the function and triggers are re-runnable (running `0622` again changes nothing). Add a micro test inserting 200 inbound messages in a loop and asserting each message causes exactly one `UPDATE` of its conversation row (compare `pg_stat_user_tables.n_tup_upd` deltas for `conversations` plus the existing `turn_count` update, documented in the test).
- [ ] **Step 2: Run** `bash <wrapper> python -m pytest tests/test_inbox_queue_triggers.py -q`; expect failures.
- [ ] **Step 3: Implement** the migration, bookkeeping (`"0622"`, README bullet describing 0620 to 0622), keeping `inbox_queue_enter` as the only code path that writes `needs_staff`.
- [ ] **Step 4: Run** the trigger file, `tests/test_inbox_queue_schema.py`, `tests/test_inbox_queue_tables_schema.py`, `tests/test_console_inbox.py` (the existing inbox behaviour must not regress; one file at a time) and `tests/test_escalation_note_schema.py`; pyright and ruff on changed Python; expect pass.
- [ ] **Step 5: Commit** `feat(inbox): queue entry function and message and escalation triggers`.

### Task 4: `csa/inbox_queue` package

**Files:**
- Create: `csa/inbox_queue/__init__.py`, `csa/inbox_queue/transitions.py`, `csa/inbox_queue/settings.py`, SQL files under `db/sql/inbox_queue/`, `tests/test_inbox_queue.py`
- Modify: `pyproject.toml` (new import-linter contract: `csa.inbox_queue` must not import `csa.console`, `csa.agents`, `csa.approval`, `csa.staff` callbacks, `csa.workspace`; it may import `csa.core` and `csa.staff.ownership`)

**Interfaces:**
- Consumes: Task 3 function; `csa.staff.ownership.lock_conversation`; `tickets/set_status.sql` and `tickets/insert_event.sql`.
- Produces in `csa/inbox_queue/transitions.py` (each takes `conn: AsyncConnection[Any]` on the caller's tenant transaction, locks the conversation first, writes one `inbox_queue_events` row, and the staff actions `claim`, `unclaim`, `assign`, `snooze`, `resolve`, `set_priority`, `set_topic` and `hand_back` also bump `inbox_version`):
  ```python
  class QueueError(Exception):            # .code: str
  async def enter(conn, client_id, conversation_id, *, reason: str, priority: str = "normal",
                  actor_user_id: UUID | None = None, claim_for: UUID | None = None) -> bool
  async def replied(conn, client_id, conversation_id, *, user_id: UUID | None) -> None
      # waiting_customer; sets last_staff_reply_at; claims for user_id if unclaimed and user_id is not None;
      # clears needs_staff fields (reason, since, due); no-op for excluded conversations
  async def held_reply_closed(conn, client_id, conversation_id) -> None
      # a held reply was rejected or expired without an answer: reason held_reply becomes customer_message
  async def hand_back(conn, client_id, conversation_id, *, actor_user_id: UUID | None) -> None
      # state bot, claim and due cleared, owned_by ai (same columns as inbox/set_owner.sql release),
      # open escalations resolved, the open escalation ticket closed with a ticket event
  async def resolve(conn, client_id, conversation_id, *, actor_user_id: UUID,
                    seen_customer_at: datetime | None) -> None   # QueueError("held_reply_pending" | "customer_wrote")
  async def snooze(conn, client_id, conversation_id, *, actor_user_id: UUID, until: datetime,
                   seen_customer_at: datetime | None) -> None    # same errors; until must be in the future
  async def claim(conn, client_id, conversation_id, *, user_id: UUID) -> None
  async def unclaim(conn, client_id, conversation_id, *, actor_user_id: UUID) -> None
  async def assign(conn, client_id, conversation_id, *, assignee_id: UUID, actor_user_id: UUID) -> None
  async def set_priority(conn, client_id, conversation_id, *, priority: str, actor_user_id: UUID) -> None
  async def set_topic(conn, client_id, conversation_id, *, topic: str, actor_user_id: UUID) -> None
  async def wake_snoozed(conn, client_id, *, limit: int = 100) -> int
      # snoozed items with snoozed_until <= now() become needs_staff (reason customer_message when the
      # customer wrote last, else manual) with a fresh clock; event `woke`; returns the count
  ```
  `resolve` is `hand_back` plus the two refusals; a `seen_customer_at` earlier than the row's `last_customer_at` raises `customer_wrote` (microsecond compare, `None` means the caller saw no customer message); a live pending approval raises `held_reply_pending`. `set_priority` recomputes `due_at` from `needs_staff_since` when the item is `needs_staff`. `claim` on an item claimed by someone else records an event noting the previous holder (it is allowed, not a lock). `assign` and `unclaim` only change the assignee fields. `csa/inbox_queue/settings.py`: `InboxSettings` Pydantic model (fields of Task 2 with the same defaults) and `async def load(conn, client_id) -> InboxSettings` returning defaults when the company has no row.

- [ ] **Step 1: Write failing tests** in `tests/test_inbox_queue.py` for each function against the real tables on synthetic data, asserting state, columns, event rows and `inbox_version` bump (or none): `enter` then `replied` (state `waiting_customer`, claim for the replying user, due cleared); `replied` with `user_id=None` does not claim; `replied` on a `bot` conversation (a person replied to a taken-over chat that never entered) makes it `waiting_customer`; `held_reply_closed` changes the reason only when it is `held_reply`; `hand_back` leaves `bot`, `owned_by='ai'`, escalation rows `resolved`, the open escalation ticket `resolved` with `closed_at` and one `ticket_events` row, and a conversation with no ticket works; `resolve` refusals (`held_reply_pending` with a live pending approval, `customer_wrote` when `last_customer_at` is newer than `seen_customer_at`) leave everything unchanged; `snooze` rejects a past `until`, hides the item, `wake_snoozed` returns it with a fresh clock and does not touch an item whose time has not come; claim of an already claimed item changes the holder and records the previous one; `unclaim`, `assign`, `set_priority` (recomputes `due_at`), `set_topic`; every function is a no-op or `QueueError("not_in_queue")` for sandbox and console chat conversations (rule: raise `not_in_queue` for staff actions, silently return for `replied`/`held_reply_closed`); `settings.load` returns defaults without a row and the row's values with one, and the Python defaults equal the table's column defaults (read from `information_schema.columns`). Add a `lint-imports` check by running it.
- [ ] **Step 2: Run** `bash <wrapper> python -m pytest tests/test_inbox_queue.py -q`; expect failures.
- [ ] **Step 3: Implement** the package, SQL files and the import-linter contract.
- [ ] **Step 4: Run** the file, `lint-imports` through the wrapper, pyright and ruff (`ruff format --check` too); expect pass.
- [ ] **Step 5: Commit** `feat(inbox): the inbox queue transitions package`.

### Task 5: Wire the transitions into the code that knows the actor

**Files:**
- Modify: `csa/approval/service.py` (`hold_reply`, `_settle`), `csa/console/inbox.py` (`mutate_conversation`), `csa/staff/callbacks.py` (`_resume_conversation`)
- Test: `tests/test_inbox_queue_wiring.py`, existing `tests/test_console_inbox.py` and the staff callbacks tests stay green

**Interfaces:**
- Consumes: Task 4 functions.
- Produces behaviour (no new public signatures):
  - `hold_reply` calls `enter(reason="held_reply")` on its connection after the approval row exists.
  - `_settle`: outcome `approved` or `edited` calls `replied(user_id=…)` after the message is inserted, where `user_id` is parsed from an actor string of the form `console_user:<uuid>` and `None` for any other actor (staff chat, `system`); outcomes `rejected` and `expired` call `held_reply_closed` and never `replied`; a settle that inserts no message (replies off, automation not allowed) calls nothing on the queue.
  - `mutate_conversation`: `takeover` (when it changes ownership) calls `enter(reason="manual", priority="normal", claim_for=actor.user_id, actor_user_id=actor.user_id)`; `reply` calls `replied(user_id=actor.user_id)` after the message and delivery rows exist; `release` (when it changes ownership) calls `hand_back(actor_user_id=actor.user_id)` after `set_owner.sql`.
  - `_resume_conversation` calls `hand_back(actor_user_id=None)` when the release statement released the conversation.
  - All calls run inside the existing transaction, so a failure rolls the whole action back.

- [ ] **Step 1: Write failing tests** in `tests/test_inbox_queue_wiring.py` (pattern of `tests/test_console_inbox.py`: `SessionWorld`, `seed`, `held` helpers): hold a reply, state `needs_staff` reason `held_reply`; approve it as a console user, state `waiting_customer` claimed by that user, `last_staff_reply_at` set; edit likewise; approve through a staff-chat actor, `waiting_customer` unclaimed; reject and let one expire, state stays `needs_staff` reason `customer_message` and the expiry's escalation row (inserted by `approval/escalate_expired.sql`) lifts the reason to `escalation` and priority to `high`; takeover of a bot conversation, `needs_staff` reason `manual` claimed by the actor, repeat takeover (`already_handled`) changes nothing; reply, `waiting_customer`; release, `bot` with the escalation ticket closed (seed an escalation through `EscalationAgent` as `test_console_inbox.py` does); staff-chat Resume AI, `bot`; an automated payment notice and a booking message inserted as `assistant` then marked `reviewed` leave an item in `needs_staff` unchanged (Review Focus 2); stale `expected_version` on takeover still returns 409 and writes no queue event.
- [ ] **Step 2: Run** `bash <wrapper> python -m pytest tests/test_inbox_queue_wiring.py -q`; expect failures.
- [ ] **Step 3: Implement** the five call sites; no other behaviour change.
- [ ] **Step 4: Run** the new file, then `tests/test_console_inbox.py`, then `tests/test_approval*.py` and `tests/test_staff*.py` files that exist (each alone); pyright and ruff on changed files; expect pass.
- [ ] **Step 5: Commit** `feat(inbox): held replies, takeover, reply and hand back drive the queue`.

### Task 6: Snooze wake job, settings route, assignee list

**Files:**
- Create: `csa/jobs/inbox_queue.py`, `tests/test_inbox_queue_api.py` (shared by Tasks 6 to 8; this task adds the settings and assignees tests)
- Modify: `csa/jobs/runner.py` (register), `csa/console/inbox.py` (routes and models), `docs/CONSOLE_API.md` later in Task 10

**Interfaces:**
- Consumes: Task 4 `wake_snoozed`, `settings.load`; pattern of `csa/bookings/api.py:373` and `csa/bookings/setup.py:574` (`GET`/`PUT /settings`, `version`, advisory lock, audit, 409 `version_conflict`).
- Produces: job `Job(name="inbox_snooze_wake", every=timedelta(minutes=1), fn=run_inbox_snooze_wake, per_client=True)` with `async def run_inbox_snooze_wake(conn, client_id, clock, *, batch_size=100) -> dict[str, Any]` returning `{"woken_count": n}`. Routes under `/inbox` (same router, same package gate and `inbox_actor` dependency): `GET /inbox/settings` returns `InboxSettingsOut` (the Task 4 fields plus `version`; defaults with `version = 0` when no row), any inbox actor; `PUT /inbox/settings` takes `InboxSettingsIn` (fields plus `version`) for tenant admin or platform admin only (tenant staff 403), enforces ranges with Pydantic (422), inserts on first save and updates after, 409 `version_conflict` on a stale version, audits `inbox.settings.updated` through `csa.console.kb_uploads.audit`; `GET /inbox/assignees` returns `list[{id: UUID, display_name: str, role: str}]` of enabled tenant admins and tenant staff of the company (from `console_memberships` and `console_users`), any inbox actor, no emails.

- [ ] **Step 1: Write failing tests** in `tests/test_inbox_queue_api.py` (harness of `tests/test_console_inbox.py`): the job wakes a due snoozed item and leaves a not-due one, runs through `build_default_registry` and the runner's `run_job_once`, and is registered every minute; settings `GET` defaults with `version == 0`, `PUT` first save then update, stale version 409, ranges 422 (each bound and `0` for the three optional hour fields), tenant staff 403 on `PUT` and 200 on `GET`, read-only 403 on both, another company's data never visible, audit row written, a `PUT` changes the `due_at` of items entering `needs_staff` afterwards (not items already waiting); assignees lists admins and staff, excludes read-only members and disabled members, a staff user can call it, another company's members are never listed.
- [ ] **Step 2: Run** `bash <wrapper> python -m pytest tests/test_inbox_queue_api.py -q`; expect failures.
- [ ] **Step 3: Implement** the job, registration, models and routes.
- [ ] **Step 4: Run** the file and `tests/test_jobs*.py` files that list the registry (each alone); pyright and ruff; expect pass.
- [ ] **Step 5: Commit** `feat(inbox): snooze wake job, inbox settings and assignee list`.

### Task 7: The queue list, filters, sort, cursor and counts

**Files:**
- Create: SQL under `db/sql/inbox/` for the new list (one file per sort: `list_queue_activity.sql`, `list_queue_waiting.sql`, `list_queue_due.sql`, `list_queue_priority.sql`, sharing one documented filter block, with a test asserting the four filter blocks are textually identical), `db/sql/inbox/queue_counts.sql`
- Modify: `csa/console/inbox.py` (models, list route, counts route), `db/sql/inbox/get_conversation.sql` and `get_conversation_detail.sql` (queue fields), `db/sql/inbox/list_conversations.sql` removed when no longer used
- Test: `tests/test_inbox_queue_api.py`, `tests/test_console_inbox.py` (existing list tests updated only where the contract changed)

**Interfaces:**
- Produces `InboxConversation` gains `queue_status`, `queue_reason`, `topic`, `priority`, `assignee_id: UUID | None`, `claimed_at`, `needs_staff_since`, `due_at`, `snoozed_until`, `last_customer_at`, `last_staff_reply_at` (receipts carry only these ids and enums). List rows and the detail read also carry `assignee: {id, display_name} | None` (a separate field, not persisted in receipts). `GET /inbox/conversations` keeps `channel`, `needs_review`, `sender`, `q`, `limit` (1 to 50) and adds `state` (repeatable, the five states), `assignee` (`me`, `none` or a UUID), `priority_min`, `reason` (repeatable), `topic` (repeatable), `overdue` (bool), `waiting_over` (minutes, 1 to 525600), `order` (`activity` default for no `state` or `state` of `resolved`/`bot`, `due` default when `state` is `needs_staff`, `waiting`, `priority`) and `before` as an **opaque cursor string**: base64url of `<sort value>|<id>`, with a bare UUID accepted as a legacy activity cursor. Filter semantics exactly as design 6.2 (AND of all; `needs_review` = live pending approval; `overdue` = `queue_status = 'needs_staff' AND due_at < now()`; `waiting_over` = `needs_staff` and `needs_staff_since < now() - n minutes`; `assignee=me` uses the actor's user id). The existing exclusions (channel `telegram`/`whatsapp`, no console chat conversations) stay. Response `InboxConversationPage`: `items`, `next_cursor: str | None`, `needs_review_count` (unchanged definition) and new `counts`. `GET /inbox/queue/counts` returns `{needs_us, mine, unassigned, overdue, waiting_customer, snoozed, oldest_waiting_at}` (mine = assigned to the actor and `needs_staff` or `waiting_customer`; unassigned = `needs_staff` with no assignee; each a count over its partial index); the list response's `counts` is the same object. The lateral per-row lookups are gone: last message text still comes from a lateral read, but only for the returned page (select the page's ids first, then join), and `known` is computed for scanned rows only when `sender` is set, otherwise for the page.
- Consumes: Tasks 1 to 4.

- [ ] **Step 1: Write failing tests** in `tests/test_inbox_queue_api.py` on synthetic conversations in every state: each filter alone and two combined (`state` multi, `assignee` me/none/uuid, `priority_min`, `reason`, `topic`, `overdue` with an item due in the past and one in the future, `waiting_over`, `channel`, `sender`, `q` name/phone/@username unchanged cases, `needs_review`); each `order` with ties on the sort value (rows with equal `due_at` and equal `last_activity_at`) paged with `limit=2` through the cursor visits every row exactly once in the documented order; a legacy UUID cursor still works; a malformed cursor is 422; exclusions (sandbox and console chat conversations never listed or counted); `counts` match a brute-force count and `needs_review_count` is unchanged; another company's rows never appear; read-only 403; the response of `takeover` (receipt) contains the new ids but no display name; the four filter blocks are identical text. Update the existing list tests in `tests/test_console_inbox.py` only where the response shape grew.
- [ ] **Step 2: Run** `bash <wrapper> python -m pytest tests/test_inbox_queue_api.py -q`; expect failures.
- [ ] **Step 3: Implement** SQL, models, parsing (a small `csa/console/inbox_cursor.py` for encode and decode with its own unit tests if it exceeds ten lines) and routes.
- [ ] **Step 4: Run** the new file, `tests/test_console_inbox.py`, `tests/test_inbox_contact.py`; pyright and ruff; expect pass.
- [ ] **Step 5: Commit** `feat(inbox): the work queue list, filters, sorts and counts`.

### Task 8: Queue actions and bulk actions

**Files:**
- Modify: `csa/console/inbox.py` (routes and models), `csa/console/contracts.py` only if a shared type is needed
- Test: `tests/test_inbox_queue_api.py`

**Interfaces:**
- Consumes: Task 4 functions, the existing `replay` and `record` idempotency helpers (`console_inbox_actions`).
- Produces routes, each taking the existing `InboxAction` body (`request_key`, `expected_version`) plus its own fields, replayable per request key, auditing `inbox.<action>`, returning `InboxResult` with the refreshed conversation: `POST /inbox/conversations/{id}/queue/claim`, `/unclaim`, `/assign` (body adds `assignee_id: UUID`; tenant admin or platform admin only, tenant staff 403; the assignee must be an enabled admin or staff member of the company, else 422), `/snooze` (adds `until: datetime`, must be in the future and at most 60 days ahead, 422 otherwise; and `seen_customer_at: datetime | None`), `/resolve` (adds `seen_customer_at`), `/priority` (adds `priority`), `/topic` (adds `topic`). A stale `expected_version` is 409 as today; `QueueError("held_reply_pending")` and `QueueError("customer_wrote")` return 409 with a JSON `detail` of the code (`{"code": "held_reply_pending"}` / `{"code": "customer_wrote"}`); `not_in_queue` is 404. `POST /inbox/bulk` body `{request_key: UUID, action: "claim"|"unclaim"|"assign"|"resolve"|"snooze"|"priority"|"topic", ids: list[UUID] (1 to 50, unique), seen_at: datetime, assignee_id?, until?, priority?, topic?}`. Each id runs in its own savepoint and reads its own current `inbox_version` (bulk carries no per-item version). `seen_at` is the time the client read the list: for `resolve` and `snooze` an item whose `last_customer_at` is later than `seen_at` is refused as `customer_wrote`. The response is `{results: [{id, outcome: "done" | "held_reply_pending" | "customer_wrote" | "not_found" | "not_in_queue"}]}` with status 200 even when some items fail, and the whole request is one replayable action. `assign` by tenant staff is 403 for the whole request.

- [ ] **Step 1: Write failing tests** in `tests/test_inbox_queue_api.py`: each single action succeeds and changes state, claims, events and `inbox_version`, and replays with the same key (no second event) and 409s with a different body under the same key; `assign` by tenant staff is 403, by admin works, to a read-only member or another company's user 422; `snooze` past and beyond 60 days 422; `resolve` with a pending held reply 409 `held_reply_pending`; with a customer message newer than `seen_customer_at` 409 `customer_wrote` and the item unchanged; claim on an item held by someone else succeeds and records the previous holder; read-only 403 and another company 404 on every route (Review Focus 5); bulk resolve of 30 items where one has a newer customer message and one a pending held reply reports 28 `done`, one `customer_wrote`, one `held_reply_pending` and changes only the 28; bulk with 51 ids or duplicates 422; bulk `assign` by tenant staff is 403 for the whole request.
- [ ] **Step 2: Run** the file; expect failures.
- [ ] **Step 3: Implement** the routes reusing `mutate`-style transaction and replay handling; keep the 32 KB body bound of `InboxRoute`.
- [ ] **Step 4: Run** the file and `tests/test_console_inbox.py`; pyright and ruff; expect pass.
- [ ] **Step 5: Commit** `feat(inbox): claim, assign, snooze, resolve, priority, topic and bulk actions`.

### Task 9: Saved views and the scale test

**Files:**
- Modify: `csa/console/inbox.py` (saved view routes)
- Create: `db/sql/inbox/saved_view_*.sql`, `tests/test_inbox_queue_scale.py`
- Test: `tests/test_inbox_queue_api.py`

**Interfaces:**
- Produces `GET /inbox/views` (the caller's views, newest first), `POST /inbox/views` (`{name: 1 to 60 chars, filters: InboxFilters}`; at most 20 per user, 409 beyond; duplicate name case-insensitively 409), `PUT /inbox/views/{id}` (rename or replace filters), `DELETE /inbox/views/{id}`; each view is private to its user (another user's id is 404); `InboxFilters` is the Pydantic model of the Task 7 query parameters (`state`, `assignee`, `priority_min`, `reason`, `topic`, `channel`, `sender`, `overdue`, `waiting_over`, `order`; never `q` and never a cursor), validated the same way, so an invalid stored filter cannot exist; `assignee` in a view may be `me`, `none` or a UUID.
- Scale test `tests/test_inbox_queue_scale.py` (marked `requires_docker`; it may take a minute): one company, 50,000 synthetic conversations generated with `generate_series` (about 800 `needs_staff` of which 100 overdue and 60 assigned to one user, 300 `waiting_customer`, 200 `snoozed`, the rest `bot` or `resolved`) with matching contacts; run `ANALYZE`; for each of the tab queries (Needs us, Mine, Unassigned, Overdue, Waiting on customer, Snoozed, Resolved, All, plus `order=waiting`) and the counts query, `EXPLAIN (FORMAT JSON)` the real query and assert the plan uses the expected index (named in the test) and contains **no Sort node**; run the `sender=known` filter on All and a name search and a phone-digit search and assert each returns a first page and the plan contains no sequential scan of `conversations` (search may scan `contacts` through its trigram index); assert correctness of counts against brute force. Assert on plans, never on wall-clock time.
- Consumes: Tasks 1, 7.

- [ ] **Step 1: Write failing tests**: saved view CRUD cases above including the 20 cap, the duplicate-name rule, privacy between two users of one company, isolation between companies, invalid filter 422, tenant staff and admin both allowed, read-only 403; the scale test.
- [ ] **Step 2: Run** `bash <wrapper> python -m pytest tests/test_inbox_queue_api.py -q` and then `tests/test_inbox_queue_scale.py -q` (one at a time); expect failures.
- [ ] **Step 3: Implement** routes and SQL; fix indexes or queries (a new migration is **not** allowed beyond 0622 in this plan: if a missing index is found, report it as BLOCKED with the plan and the evidence so the controller can amend Task 1's migration before it ships) until the scale test passes.
- [ ] **Step 4: Run** both files again; pyright and ruff; expect pass.
- [ ] **Step 5: Commit** `feat(inbox): saved views and the 50,000 conversation scale test`.

### Task 10: Documentation and whole-branch checks

**Files:**
- Modify: `docs/CONSOLE_API.md` (the Inbox sections: "Telegram customer inbox" and "Inbox read fields, filters, search and order", plus the new routes, queue fields, states, filters, cursor, counts, actions, bulk, views, settings, assignees and the job), `docs/console_openapi.json` (regenerated), `docs/PROGRESS.md`, `docs/superpowers/specs/2026-11-16-inbox-refinement-design.md` (numbering: 0621 holds settings, events and saved views; 0622 the function and triggers; the escalation trigger replaces the explicit escalation call; PR 1 is split into 1a backend and 1b UI), an Inbox retest document or the existing retest doc's Inbox section with manual rows for the owner (marked not run): escalation appears in Needs us with a due time, takeover creates a `manual` item, reply moves it to waiting on customer, a customer message returns it, resolve hands back, snooze returns the item, held reply, claim and assign, role limits.

- [ ] **Step 1: Docs** as listed; regenerate with `bash <wrapper> python scripts/export_console_openapi.py` and check `git diff --stat docs/console_openapi.json` shows only the Inbox additions.
- [ ] **Step 2: Checks**: `git diff origin/main --stat -- db/migrations` lists exactly 0620, 0621, 0622 and the README; run each changed or added Python test file one at a time (`test_inbox_queue_schema`, `_tables_schema`, `_triggers`, `test_inbox_queue`, `_wiring`, `_api`, `_scale`, `test_console_inbox`, `test_inbox_contact`, `test_console_contract_snapshot`), pyright on the whole project through the wrapper (CI runs it on everything), `ruff check`, `ruff format --check`, `lint-imports`, `bash <wrapper> python -m pytest tests/test_migrations*.py -q` if such files exist (each alone).
- [ ] **Step 3: Commit** `docs: inbox queue API, retest rows and progress`.
- [ ] **Step 4: Whole-branch review** on the most capable model, one fix wave, one scoped re-review; merge main into the branch; push; open the PR with `gh api -X POST repos/amsyrzmi/CSAV2/pulls --input <json>` (body: what it adds, the migrations and that a staging rollout stops all four services for them, no customer-facing change so no golden harness, UI follows in 1b, rulings and parked minors); watch CI to green; tell the owner the PR number. No rollout by this session.
