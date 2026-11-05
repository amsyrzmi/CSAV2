-- The pending changes that ask to move a booking, locked.
SELECT id FROM public.bookings
WHERE client_id = %(client_id)s AND replaces_booking_id = %(id)s AND status = 'pending'
ORDER BY id
FOR UPDATE;
