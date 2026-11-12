-- Unlink: puts back what the link changed on one contact and makes it live again. The phone (with
-- its verified stamp) and telegram_id go back to their values before the link only while they
-- still hold what the link left there (linked_*); a value changed since stays. A verified phone or
-- a Telegram account another contact of the company holds by now is left out (the contact comes
-- back without the stamp, or without the account) instead of breaking a unique index.
WITH want AS (
    SELECT c.id,
           CASE WHEN as_linked.phone THEN %(phone)s::text ELSE c.phone END AS phone,
           CASE WHEN as_linked.phone THEN %(phone_verified_at)s::timestamptz
                ELSE c.phone_verified_at END AS phone_verified_at,
           CASE WHEN as_linked.telegram THEN %(telegram_id)s::bigint
                ELSE c.telegram_id END AS telegram_id
    FROM public.contacts c
    CROSS JOIN LATERAL (
        SELECT c.phone IS NOT DISTINCT FROM %(linked_phone)s::text
                   AND c.phone_verified_at
                       IS NOT DISTINCT FROM %(linked_phone_verified_at)s::timestamptz AS phone,
               c.telegram_id IS NOT DISTINCT FROM %(linked_telegram_id)s::bigint AS telegram
    ) AS as_linked
    WHERE c.client_id = %(client_id)s AND c.id = %(id)s
)
UPDATE public.contacts c
   SET phone = w.phone,
       phone_verified_at = CASE
           WHEN EXISTS (
               SELECT 1 FROM public.contacts o
                WHERE o.client_id = c.client_id AND o.id <> c.id AND o.merged_into IS NULL
                  AND o.phone_verified_at IS NOT NULL
                  AND o.phone_digits <> ''
                  AND o.phone_digits = public.my_phone_digits(w.phone)
           ) THEN NULL
           ELSE w.phone_verified_at END,
       telegram_id = CASE
           WHEN EXISTS (
               SELECT 1 FROM public.contacts o
                WHERE o.client_id = c.client_id AND o.id <> c.id
                  AND o.telegram_id = w.telegram_id
           ) THEN NULL
           ELSE w.telegram_id END,
       merged_into = NULL
  FROM want w
 WHERE c.client_id = %(client_id)s AND c.id = w.id
RETURNING c.phone, c.phone_verified_at, c.telegram_id;
