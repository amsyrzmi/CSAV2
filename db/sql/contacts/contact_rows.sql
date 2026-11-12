-- The contacts of a link or unlink, row-locked after their advisory locks. FOR UPDATE also waits
-- for any transaction that has just written a row pointing at one of them, so those rows are
-- committed before the link moves them.
SELECT c.id, c.phone, c.phone_verified_at, c.telegram_id, c.merged_into, c.created_at,
       EXISTS (
           SELECT 1 FROM public.subscriptions s
            WHERE s.client_id = c.client_id AND s.contact_id = c.id
       ) AS has_subscription
FROM public.contacts c
WHERE c.client_id = %(client_id)s
  AND c.id = ANY (%(ids)s)
ORDER BY c.id
FOR UPDATE OF c;
