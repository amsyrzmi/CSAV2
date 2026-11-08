# Migrations

Ordered, additive SQL files applied by `scripts/migrate.py` in filename order.
`apps/gateway/main.py::EXPECTED_SCHEMA_VERSION` must equal the highest shipped
version; tests import it from there and never hardcode it.

Reserved ranges (client growth program, 2026-10-27):

- `0470`–`0479`: lane A (WhatsApp channel, support refinements).
- `0480`–`0489`: lane B (subscriptions, HR access page, invitation mail).
- `0490`–`0499`: TikTok Shop connect (own track).
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

A migration that rebuilds `console_proposals_kind_check` or `console_workspace_turns_action_check`
(the assistant's proposal kinds and turn actions) must be numbered above 0531, or must read the
current list with `pg_get_constraintdef` and add to it, as 0531 does. A fixed list in a migration
numbered below 0531 is repaired by 0531 on a fresh database, but on a database already past 0531
that migration runs after 0531 is stamped and removes `booking_action`, with nothing failing until
the first booking card. `tests/test_bookings_bot_schema.py` scans the migrations for this.

Whoever merges a migration raises `EXPECTED_SCHEMA_VERSION` to the highest
merged version. Lanes never reference each other's tables.
