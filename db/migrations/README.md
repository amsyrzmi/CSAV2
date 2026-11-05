# Migrations

Ordered, additive SQL files applied by `scripts/migrate.py` in filename order.
`apps/gateway/main.py::EXPECTED_SCHEMA_VERSION` must equal the highest shipped
version; tests import it from there and never hardcode it.

Reserved ranges (client growth program, 2026-10-27):

- `0470`–`0479`: lane A (WhatsApp channel, support refinements).
- `0480`–`0489`: lane B (subscriptions, HR access page, invitation mail).
- `0500`–`0509`: console redesign (Telegram username on the contact, inbox list indexes in 0501,
  the `console_last_change` audit read function and its target index in 0502, the orders
  `(client_id, created_at)` index for the business order filters in 0503).
- `0510`–`0519`: bookings (own booking core: people, resources, booking types, hours, blocks,
  holds and the leave read function in 0510; the team-members read for setup in 0511; the bot's
  columns, the settings and reminders tables and the `booking_pending` event in 0512). Ranges kept
  by the settings: `reminder_minutes` 15 to 10080, `pending_hours` 1 to 168, `customer_limit` 1 to
  50 (checked by the API and by the table).
- `0520`–`0529`: HR people (People & HR project 1).
- `0531`: Bookings assistant action, placed after 0530 because these two constraints were last
  redefined in 0521 (`console_proposals_kind_check` and `console_workspace_turns_action_check` also
  allow `booking_action`). It reads each constraint's current list and adds to it, so it keeps
  whatever other tracks allow.

Whoever merges a migration raises `EXPECTED_SCHEMA_VERSION` to the highest
merged version. Lanes never reference each other's tables.
