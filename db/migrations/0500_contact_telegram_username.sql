-- Telegram @username on the contact, shown in the customer inbox and matched by its search.
-- Adds contacts.telegram_username (nullable) with a format check mirroring Telegram's rules
-- (5 to 32 characters in practice; 4 is allowed for legacy handles). The gateway validates the
-- value before writing and stores NULL for anything that does not match, so the check can never
-- reject a message. No data backfill: the column fills as customers write in.
-- Pre-check: contacts exists. Safe to run twice: ADD COLUMN IF NOT EXISTS (the named check is
--   part of the column definition, so it is created once with the column).
-- Rollback: ALTER TABLE public.contacts DROP COLUMN telegram_username;

ALTER TABLE public.contacts
    ADD COLUMN IF NOT EXISTS telegram_username text
    CONSTRAINT contacts_telegram_username_format CHECK (telegram_username ~ '^[A-Za-z0-9_]{4,32}$');
