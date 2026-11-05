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
  holds and the leave read function in 0510; the team-members read for setup in 0511).
- `0520`–`0529`: HR people (People & HR project 1).

Whoever merges a migration raises `EXPECTED_SCHEMA_VERSION` to the highest
merged version. Lanes never reference each other's tables.
