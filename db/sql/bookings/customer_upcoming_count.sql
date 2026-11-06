-- How many upcoming bookings a customer holds. A private meeting is never counted, and neither is a
-- pending change: it replaces a booking that is already counted.
SELECT count(*) AS n FROM public.bookings
WHERE client_id = %(client_id)s AND contact_id = %(contact_id)s AND NOT private
  AND status IN ('pending', 'confirmed') AND replaces_booking_id IS NULL
  AND ends_at > %(now)s;
