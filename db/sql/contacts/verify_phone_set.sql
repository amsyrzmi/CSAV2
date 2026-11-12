-- Stamps a channel-verified phone on a live contact. The same digits keep the phone as written and
-- the first stamp; a contact with a different verified phone is left alone (no row returned).
UPDATE public.contacts
   SET phone = CASE WHEN phone_digits = public.my_phone_digits(%(phone)s) THEN phone
                    ELSE %(phone)s END,
       phone_verified_at = CASE
           WHEN phone_verified_at IS NOT NULL
            AND phone_digits = public.my_phone_digits(%(phone)s) THEN phone_verified_at
           ELSE now() END
 WHERE client_id = %(client_id)s
   AND id = %(contact_id)s
   AND merged_into IS NULL
   AND (phone_verified_at IS NULL OR phone_digits = public.my_phone_digits(%(phone)s))
RETURNING id, phone, phone_verified_at, telegram_id;
