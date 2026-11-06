-- Hold a booking still while its reminders are written: a move or cancel (FOR UPDATE) waits for
-- this, and this waits for one already under way, so the reminder is written for the time that
-- stands.
SELECT id FROM public.bookings WHERE client_id = %(client_id)s AND id = %(id)s FOR SHARE;
