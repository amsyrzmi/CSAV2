-- A person is now linked to another member: the old member's connection to them goes, with the
-- rows that followed it (they were for the old member's calendar), so the new one is filled again.
WITH gone AS (
    DELETE FROM public.booking_calendar_connections
     WHERE client_id = %(client_id)s AND person_id = %(person_id)s AND user_id <> %(user_id)s
    RETURNING person_id
)
DELETE FROM public.booking_calendar_events e
 USING gone
 WHERE e.client_id = %(client_id)s AND e.person_id = gone.person_id;
