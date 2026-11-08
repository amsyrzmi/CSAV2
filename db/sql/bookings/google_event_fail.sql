-- A try at %(version)s failed, with a category (never text from Google). Failures count against
-- the version they were tried at; a booking that changed again starts a fresh count.
UPDATE public.booking_calendar_events
   SET status = 'failed',
       attempts = CASE WHEN failed_version IS NOT DISTINCT FROM %(version)s THEN attempts + 1
                       ELSE 1 END,
       failed_version = %(version)s,
       last_error = %(error)s,
       updated_at = now()
 WHERE client_id = %(client_id)s AND booking_id = %(booking_id)s AND person_id = %(person_id)s
   AND status <> 'dropped';
