-- The contact being verified (merged or not) and every other live contact of the company with the
-- same phone digits, verified or staff-entered, oldest first.
SELECT id, id = %(contact_id)s AS is_self, phone_digits, phone_verified_at, merged_into
FROM public.contacts
WHERE client_id = %(client_id)s
  AND (id = %(contact_id)s OR (merged_into IS NULL AND phone_digits = %(digits)s))
ORDER BY created_at, id;
