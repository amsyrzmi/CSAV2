-- What a customer message's key already did: the booking it made, moved or cancelled, and whose
-- that booking is, so a replay answers only the customer it belongs to.
SELECT a.booking_id, a.action, b.contact_id, b.replaces_booking_id
FROM public.booking_turn_actions a
JOIN public.bookings b ON b.client_id = a.client_id AND b.id = a.booking_id
WHERE a.client_id = %(client_id)s AND a.turn_key = %(turn_key)s;
