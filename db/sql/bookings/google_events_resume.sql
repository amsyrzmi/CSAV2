-- The person connected again: what was dropped is out of date again, and what failed gets its tries
-- back (it may have failed because the grant was already gone). The reconcile job brings them in
-- line (patching the event it already has, or making one).
UPDATE public.booking_calendar_events
   SET status = 'pending', attempts = 0, last_error = NULL, updated_at = now()
 WHERE client_id = %(client_id)s AND person_id = %(person_id)s
   AND status IN ('dropped', 'failed');
