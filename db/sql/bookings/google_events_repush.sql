-- The person's Bookings calendar is a new one: the events of the old one went with it, so every
-- row that said an event was there is out of date, and the reconcile job makes them again.
UPDATE public.booking_calendar_events
   SET status = 'pending', attempts = 0, last_error = NULL, updated_at = now()
 WHERE client_id = %(client_id)s AND person_id = %(person_id)s
   AND status IN ('synced', 'failed');
