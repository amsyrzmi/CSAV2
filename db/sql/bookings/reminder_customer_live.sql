-- The customer already has a reminder for this start, by any channel, that was not dropped. One
-- reminder per customer and start, so a chat that appears later does not add a second one.
SELECT 1
FROM public.booking_reminders
WHERE client_id = %(client_id)s AND booking_id = %(booking_id)s AND starts_at = %(starts_at)s
  AND audience = 'customer' AND status <> 'dropped'
LIMIT 1;
