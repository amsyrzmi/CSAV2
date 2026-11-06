-- The customer's own bookings, upcoming first and soonest first, so their next booking is never
-- pushed out by older ones. Never a private meeting, and a pending booking only when the bot made
-- it (a pending one a member of staff made is theirs to deal with, not the customer's).
SELECT id, type_id, status, starts_at, ends_at, replaces_booking_id
FROM public.bookings
WHERE client_id = %(client_id)s AND contact_id = %(contact_id)s
  AND NOT private
  AND (status = 'confirmed' OR (status = 'pending' AND source = 'bot'))
ORDER BY (ends_at < %(now)s),
         CASE WHEN ends_at >= %(now)s THEN starts_at END ASC,
         starts_at DESC
LIMIT 20;
