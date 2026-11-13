-- The email a customer gave at sign-up (for payment reminders), put on their contact only while it
-- has no address at all. It is a claim nobody has checked, so it goes into customer_given_email,
-- never into email, which identity (the walk-in match, the Inbox's "known", contact linking)
-- trusts; an address already on file is never replaced.
UPDATE public.contacts
SET customer_given_email = %(email)s::text
WHERE client_id = %(client_id)s AND id = %(contact_id)s
  AND nullif(btrim(email), '') IS NULL
  AND nullif(btrim(customer_given_email), '') IS NULL;
