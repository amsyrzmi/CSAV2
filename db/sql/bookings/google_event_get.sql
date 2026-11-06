-- A person's ledger row for a booking, if there is one.
SELECT status, google_event_id, synced_version, attempts, failed_version
FROM public.booking_calendar_events
WHERE client_id = %(client_id)s AND booking_id = %(booking_id)s AND person_id = %(person_id)s;
