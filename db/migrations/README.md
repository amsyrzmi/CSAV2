# Migrations

Ordered, additive SQL files applied by `scripts/migrate.py` in filename order.
`apps/gateway/main.py::EXPECTED_SCHEMA_VERSION` must equal the highest shipped
version; tests import it from there and never hardcode it.

Reserved ranges (client growth program, 2026-10-27):

- `0470`–`0479`: lane A (WhatsApp channel, support refinements).
- `0480`–`0489`: lane B (subscriptions, HR access page, invitation mail).
- `0490`–`0499`: TikTok Shop connect and the channels work. 0494 adds two partial unique indexes on
  `channel_credentials`: one active Telegram bot per company and one company per bot.
- `0500`–`0509`: console redesign (Telegram username on the contact, inbox list indexes in 0501,
  the `console_last_change` audit read function and its target index in 0502, the orders
  `(client_id, created_at)` index for the business order filters in 0503).
- `0510`–`0519`: bookings (own booking core: people, resources, booking types, hours, blocks,
  holds and the leave read function in 0510; the team-members read for setup in 0511; the bot's
  columns, the settings and reminders tables and the `booking_pending` event in 0512). Ranges kept
  by the settings: `reminder_minutes` 15 to 10080, `pending_hours` 1 to 168, `customer_limit` 1 to
  50 (checked by the API and by the table). Stage 2 (Google Calendar) adds 0513: a person's
  connection, the PKCE states of connecting, and the ledger of what Google holds for each booking
  (`booking_calendar_connections`, `booking_calendar_oauth_states`, `booking_calendar_events`).
  Tables only, so it can arrive on a database already past 0531; the highest version stays 0531.
- `0520`–`0529`: HR people (People & HR project 1).
- `0531`: Bookings assistant action, placed after 0530 because these two constraints were last
  redefined in 0521 (`console_proposals_kind_check` and `console_workspace_turns_action_check` also
  allow `booking_action`). It reads each constraint's current list and adds to it, so it keeps
  whatever other tracks allow.
- `0540`–`0549`: training library. 0540 adds the `training_library` tab to
  `console_workspace_turns_page_key_check`, reading the live list as 0531 does; 0541 adds
  `training_items` (tenant isolated, `csa_app` only) and `training_is_hr_owner()`, the
  no-argument definer check that reads `app.client_id` and `app.hr_user_id`.
- `0542`: assistant engine. `console_proposals_kind_check` also allows `plan`,
  `console_workspace_turns_action_check` also allows `plan` and `email_batch` (both read from the
  live list as 0531 does), and `email_drafts.facts_used` (`jsonb`) holds the facts a drafted email
  used, readable by the draft's owner and never on a card.
- `0550`–`0559`: knowledge cleanup. 0550 adds the `cancelled` status to
  `kb_ingest_jobs_status_check` (read from the live list as 0540 does) and `kb_ingest_jobs.dismissed_at`,
  and cancels and dismisses the existing jobs whose files were all removed.
- `0560`–`0569`: customer bot answers. 0560 adds `escalations.note` (staff context note, at most 500
  characters) and the `escalations_conversation_created` index.
- `0570`–`0579`: credit monitoring. 0570 adds `ai_usage_events`, the ledger of model calls
  (tenant isolated, `csa_app` insert and select, `csa_ro` select, never updated or purged). 0571 adds
  the global, append-only billing tables `ai_model_prices`, `billing_fx_rates`, `billing_stage_fees`
  and `billing_markup_tiers` (effective-dated; a trigger refuses UPDATE and DELETE even for the
  owner, `csa_app` has select and insert only) and seeds the fees, markup tiers, the two default
  models' prices and the USD to MYR rate. 0572 adds `client_billing_plans`, each company's
  append-only plan history (stage and bundle from a Kuala Lumpur date; tenant isolated, `csa_app`
  select and insert only, `csa_ro` select, a trigger refuses UPDATE and DELETE even for the
  owner; inserted by platform admins through `csa.billing.plans.insert_plan`, which enforces the
  plan rules and audits each row in `console_audit`).
- `0580`–`0589`: customer onboarding (sign-up from the chat, one customer across channels). 0580 adds
  `signup_requests`, `subscription_settings` (pending expiry days), `plans.signup_mode` and
  `plans.is_signup_open`, `subscriptions.activated_at`, and the `pending` status in
  `subscriptions_status_chk` (read from the live list as 0531 does). 0581 adds
  `public.my_phone_digits(text)`, the generated `contacts.phone_digits` (rewrites `contacts`),
  `contacts.phone_verified_at` and `merged_into`, the unique `contacts_verified_phone_uq`, and the
  `contact_links` and `contact_link_reviews` tables, and lets `csa_app` update
  `agent_preparation_requests.contact_id` only (a link moves staged requests to the survivor).
- `0590`–`0599`: payments. 0590 gives `invoices` a status check, an amount check, a currency check (three
  upper-case letters), a `reference` (unique per company), `method_kind`, `subscription_id` with `period_start` and `period_end` (one invoice per
  subscription period, void ones included), a claim and a void trail, and the keys
  `invoices_client_id_key` and `subscriptions_client_id_key` on `(client_id, id)`; it adds
  `subscriptions.pause_reason` and the tables `payment_settings`, `payment_methods`, `payments` (the
  ledger, append-only for `csa_app`, one payment per invoice and per idempotency key) and
  `invoice_reminders`, all tenant isolated with grants to `csa_app` only. 0591 seeds the `payment_agent`
  capability row for every company (switched on where `subscription_agent` is) and adds
  `signup_requests.email`.
- `0600`–`0609`: video in the training library. 0600 widens `training_items` (kind `pdf` or `video`,
  `view_mode`, `page_count` and `sha256` nullable, `status`, `duration_ms`, `container`, `speech`,
  `transcript_language`, `failure_code`, `video_codec`, `audio_codec`, the shape and ready checks, and
  `UNIQUE (client_id, id)`; the new checks are added `NOT VALID` then validated). 0601 adds
  `training_video_uploads`, `training_video_pieces`, `training_video_jobs`,
  `training_transcript_segments`, `training_transcript_windows` (vector(384)) and
  `training_video_usage` (no item foreign key, so deleting a video keeps its seconds counted).
- `0610`–`0619`: pictures. 0610 adds `media_assets` (one picture per `(client_id, kind, subject_key)`,
  kind `payment_qr`, `plan`, `product` or `training_thumbnail`; the metadata, `sha256` is the version)
  and `media_asset_data` (the bytes, keyed through `(client_id, asset_id)` so they cannot pair with
  another company's asset), both tenant isolated with grants to `csa_app`; `csa_ro` can read
  `media_assets` only.

- `0611`: the staff assistant's page-key check also allows `payments` and `connections` (the Overview
  assistant change added both tabs; same read-then-rebuild method as 0540).
- `0630`-`0649`: HR processes. 0630 adds the process tables (`hr_process_templates`,
  `hr_process_template_rules`, `hr_process_template_steps`, `hr_processes`, `hr_process_tasks`,
  `hr_process_events`; tenant isolated, no grant to any application role, read and written only through
  definer functions from 0631; the event log is immutable) and `hr_employees.probation_end`. 0631 adds
  the template write path `hr_template_apply` (create, update, deactivate; HR owner or `people.manage`,
  receipt in `hr_operations`, one `console_audit` row per change, tie conflicts and late leaver steps
  returned as warnings), the reads `hr_template_rows` and `hr_template_get`, and the internal
  `hr_process_match` (most specific active template of the kind, then the oldest).
  0632 adds the trigger `hr_process_on_employee` on `hr_employees` (onboarding on add or rehire,
  offboarding on leave, cancel on undo, due dates follow `start_date`, `last_day` and `probation_end`,
  manager tasks follow the manager, the leaver's tasks closed when the leaver finishes; it runs in the
  writer's transaction), `hr_process_settle` (the completion rule), and the HR entry points
  `hr_process_start_apply`, `hr_process_cancel_apply` and `hr_probation_set`.
  0633 adds the reads `hr_process_rows` (filters, keyset paging newest first), `hr_process_get`,
  `hr_task_rows_mine` (own tasks, plus the HR pool for the HR team) and `hr_person_processes` (anchor
  dates and "no matching template" per kind, counted within the current employment); the HR team
  sees everything, a manager the processes of direct reports without notes, actors or events, the
  person their own onboarding and offboarding with progress and only their own tasks (not custom
  processes about them), an assignee only their own tasks. It also adds the indexes
  `hr_processes_by_start` and `hr_employees_by_manager`.
  0634 adds `hr_task_apply` (complete, skip, approve, send back, note, reschedule, reassign, close
  anyway; the tenant's HR operations lock first, receipt in `hr_operations`, one event per action with
  the real actor; approvals by the HR team or the person's manager, never by the person who completed
  the step except the HR owner with no one else, flagged; a manager approves only when they can act,
  else the HR team; an HR member never approves a step of a process about themself, with the same
  exception; automatic checks on complete), the internal `hr_task_reminder_user` (who can act for a
  person: enabled, not read-only membership and enabled account; also 0635's reminder rule),
  `hr_process_tasks.assignee_overridden` (HR's reassignment survives a manager change) and
  `sent_back_reason`, and `hr_task_rows_mine` again with `needs_my_approval`, `sent_back_reason`,
  `mine`, `assignee_name` and `done_by_hr` (dropped and made again for the new columns). It
  changes three 0632 functions: a person step whose named owner has left starts in the HR pool, a
  leaver's own onboarding and custom processes are cancelled, and a leaver's open steps in other
  people's processes go to the HR pool instead of being skipped.
  0635 adds the task reminders: `hr_notifications_kind_check` also allows `task_assigned`,
  `task_due_soon`, `task_due` and `task_overdue` (read from the live list, as 0611),
  `hr_notifications.due_on` and `subject` (the plain line), `hr_tasks_due` (the reminders due on a
  day, every tenant for the worker or the session's own: the assignee's console account, else one
  row per HR team member; at most 5000), `hr_tasks_due_tenants` (the worker's tenant list, without
  that cap), the internal uncapped `hr_tasks_due_all` both are built on, and `hr_task_reminder_put`
  (the system's idempotent writer, called only by `csa.hr.reminders.emit`); `hr_task_rows_mine`
  (same columns) also lists, for the HR team, the steps of a person whose reminders go to the HR pool.
  0636 adds the template editor's read `hr_template_preview` (how many active people a draft rule set
  applies to, the active templates it ties with; HR team only) on the internal
  `hr_template_rules_parse` (0631's rule validation, which `hr_template_write_rules` now calls) and
  `hr_template_ties` (0631's tie rule, which `hr_template_conflicts` now calls), and changes
  `hr_process_start_apply`: a manual start meeting an active process of the kind is refused
  (`hr_process_already_active`) unless the body says `replace: true`.
  0637 (the assistant's "mark my task done" card, slice 1c-ii) adds `hr_task` to
  `console_proposals_kind_check` and `hr_task_complete` to `console_workspace_turns_action_check`,
  read from the live lists as 0542 does; no table, column, grant or function changes.

- `0620`–`0629`: Customer Inbox refinement. 0620 adds the queue columns on `conversations`, their
  checks, the activity and partial indexes the queue tabs and counts read, and the backfill function
  `inbox_queue_backfill` (migration only: no role but the owner may run it); it builds no trigram
  index (the app role reads `contacts` under row security, where `ILIKE` cannot use one, so contact
  search is a bounded per-company scan); 0621 adds the tenant tables
  `inbox_settings`, `inbox_queue_events` (append-only for `csa_app`) and `inbox_saved_views`; 0622
  adds `inbox_queue_enter` (the only way into `needs_staff`) and the message and escalation triggers
  that keep the queue state (one conversation update per message, never `inbox_version`); 0623
  adds `reminders`, the generic timer table behind `csa.reminders` (tenant row security, no
  foreign key but the company; `(client_id, dedupe_key)` is unique); 0624 adds
  `console_notifications` (one row per person and notification, tenant row security,
  `(client_id, user_id, dedupe_key)` unique, `(user_id, client_id)` references
  `console_memberships` as `hr_notifications` does) and the SECURITY DEFINER recipient functions
  `inbox_team_user_ids`, `inbox_admin_user_ids` and `console_notification_members` (EXECUTE to
  `csa_app` only, each refusing a company other than `app.client_id`); 0625 adds
  `staff_notification_targets.language` (`en` or `ms`, default `en`: the language of the staff-chat
  message) and appends `inbox.overdue` to the events of every enabled target that lacks it (the
  `events` column default is not changed, `tests/test_bookings_bot_schema.py` pins it; a chat
  connected later opts in on PR 3's targets page); 0626 adds `console_notification_prefs` (one row
  per person, written only when they save: email on or off, the six inbox kinds, the priority
  floor, a once-per-episode failure flag) and `console_email_outbox` (the person email queue, one
  row per notification, `notification_id` unique and not a foreign key), both with tenant row
  security, the columns `staff_notification_targets.failure_notified_at` and `version` (the edit
  counter) and `staff_notifications.held_at` (quiet-hours hold), the `cancelled` staff
  notification status (the live constraint is read and extended, as 0531 does), and the SECURITY
  DEFINER lookup `console_member_emails` (EXECUTE to `csa_app` only). Later numbers are listed as
  they ship.

A migration that rebuilds `console_proposals_kind_check` or `console_workspace_turns_action_check`
(the assistant's proposal kinds and turn actions) must be numbered above 0531, or must read the
current list with `pg_get_constraintdef` and add to it, as 0531 does. A fixed list in a migration
numbered below 0531 is repaired by 0531 on a fresh database, but on a database already past 0531
that migration runs after 0531 is stamped and removes `booking_action`, with nothing failing until
the first booking card. `tests/test_bookings_bot_schema.py` scans the migrations for this.

Likewise, a migration that rebuilds `hr_notifications_kind_check` must read the live list with
`pg_get_constraintdef` and add to it (the 0611 and 0635 method), never a fixed list: an Inbox-lane
migration numbered 0620-0629 that merges after 0635 runs after it (the migrator fills gaps), and a
fixed list would fail on the task reminder rows or drop the `task_*` kinds.

Whoever merges a migration raises `EXPECTED_SCHEMA_VERSION` to the highest
merged version. Lanes never reference each other's tables.
