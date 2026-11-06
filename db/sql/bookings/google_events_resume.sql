-- The person connected again: what was dropped is out of date again, and the reconcile job brings
-- it in line (patching the event it already has, or making one).
UPDATE public.booking_calendar_events
   SET status = 'pending', attempts = 0, last_error = NULL, updated_at = now()
 WHERE client_id = %(client_id)s AND person_id = %(person_id)s AND status = 'dropped';
