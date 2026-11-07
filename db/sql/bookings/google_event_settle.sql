-- The event now matches the booking as read at %(version)s: synced, or deleted when none is wanted.
-- A late answer never records an older version over a newer one (no row comes back then).
UPDATE public.booking_calendar_events
   SET status = %(status)s,
       google_event_id = %(event_id)s,
       synced_version = %(version)s,
       failed_version = NULL,
       attempts = 0,
       last_error = NULL,
       updated_at = now()
 WHERE client_id = %(client_id)s AND booking_id = %(booking_id)s AND person_id = %(person_id)s
   AND status <> 'dropped'
   AND (synced_version IS NULL OR synced_version <= %(version)s)
RETURNING booking_id;
