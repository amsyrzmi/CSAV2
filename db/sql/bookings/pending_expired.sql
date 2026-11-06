-- The customer's bot bookings that have lapsed: pending, with an expiry, and either past it or
-- already started (a booking moved earlier keeps the expiry it was made with). Staff's own pending
-- bookings have no expiry and are never here. Locked, and skipped when someone else holds one.
SELECT id
FROM public.bookings
WHERE client_id = %(client_id)s AND source = 'bot' AND status = 'pending'
  AND expires_at IS NOT NULL
  AND (expires_at <= %(now)s OR starts_at <= %(now)s)
ORDER BY expires_at, id
LIMIT %(batch_size)s
FOR UPDATE SKIP LOCKED;
