-- A person's ledger row for a booking: made (with the event id we will use) the first time, and
-- locked and returned every time. An id already stored stays; one made now fills a missing one.
INSERT INTO public.booking_calendar_events (client_id, booking_id, person_id, google_event_id)
VALUES (%(client_id)s, %(booking_id)s, %(person_id)s, %(event_id)s)
ON CONFLICT (client_id, booking_id, person_id) DO UPDATE
   SET google_event_id = coalesce(public.booking_calendar_events.google_event_id,
                                  EXCLUDED.google_event_id)
RETURNING status, google_event_id, synced_version, attempts, failed_version;
