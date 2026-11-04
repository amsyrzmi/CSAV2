-- Set the contact's Telegram username; the value is the normalised one (NULL when the sender has none
-- or an invalid one). Writes only when it differs. Parameters: contact_id, username, client_id.
UPDATE public.contacts
SET telegram_username = %(username)s::text
WHERE id = %(contact_id)s
  AND client_id = %(client_id)s
  AND telegram_username IS DISTINCT FROM %(username)s::text
