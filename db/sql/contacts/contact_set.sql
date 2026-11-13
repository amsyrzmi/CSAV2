-- The columns a link changes on a contact, and an unlink puts back.
UPDATE public.contacts
   SET phone = %(phone)s,
       phone_verified_at = %(phone_verified_at)s::timestamptz,
       telegram_id = %(telegram_id)s,
       merged_into = %(merged_into)s
 WHERE client_id = %(client_id)s AND id = %(id)s;
