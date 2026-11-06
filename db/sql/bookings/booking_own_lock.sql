-- Lock a booking the customer may act on: their own contact's, never a private meeting, and never
-- a pending booking a member of staff made. Anything else is not found, as if it did not exist.
SELECT id, status, replaces_booking_id
FROM public.bookings
WHERE client_id = %(client_id)s AND id = %(id)s AND contact_id = %(contact_id)s
  AND NOT private AND NOT (status = 'pending' AND source <> 'bot')
FOR UPDATE;
