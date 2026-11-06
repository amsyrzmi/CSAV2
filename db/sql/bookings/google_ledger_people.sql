-- Where each person's Google event stands for one booking, and whether their mail left out the
-- calendar file: read after the push, to decide who gets a file.
SELECT person_id, status, synced_version, file_free
FROM public.booking_calendar_events
WHERE client_id = %(client_id)s AND booking_id = %(booking_id)s;
