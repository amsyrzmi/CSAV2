-- The booking a customer message already made, by that message's key.
SELECT id, contact_id FROM public.bookings
WHERE client_id = %(client_id)s AND turn_key = %(turn_key)s;
