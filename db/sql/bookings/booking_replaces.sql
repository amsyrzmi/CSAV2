-- The booking a pending request would move, read without a lock so a caller can lock that one first.
SELECT replaces_booking_id FROM public.bookings WHERE client_id = %(client_id)s AND id = %(id)s;
