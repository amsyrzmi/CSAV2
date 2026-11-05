-- How many upcoming bookings a customer holds. A pending change replaces a booking that is already
-- counted, so it is left out.
SELECT count(*) AS n FROM public.bookings
WHERE client_id = %(client_id)s AND contact_id = %(contact_id)s
  AND status IN ('pending', 'confirmed') AND replaces_booking_id IS NULL
  AND ends_at > %(now)s;
