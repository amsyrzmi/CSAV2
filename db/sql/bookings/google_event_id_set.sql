-- A new event id for a row whose old one Google keeps reserved (the event was deleted there).
UPDATE public.booking_calendar_events
   SET google_event_id = %(event_id)s, updated_at = now()
 WHERE client_id = %(client_id)s AND booking_id = %(booking_id)s AND person_id = %(person_id)s;
