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

A migration that rebuilds `console_proposals_kind_check` or `console_workspace_turns_action_check`
(the assistant's proposal kinds and turn actions) must be numbered above 0531, or must read the
current list with `pg_get_constraintdef` and add to it, as 0531 does. A fixed list in a migration
numbered below 0531 is repaired by 0531 on a fresh database, but on a database already past 0531
that migration runs after 0531 is stamped and removes `booking_action`, with nothing failing until
the first booking card. `tests/test_bookings_bot_schema.py` scans the migrations for this.

Whoever merges a migration raises `EXPECTED_SCHEMA_VERSION` to the highest
merged version. Lanes never reference each other's tables.
