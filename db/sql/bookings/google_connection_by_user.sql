-- A member's own connection and its state. Never returns a token.
SELECT id, person_id, status, calendar_id
FROM public.booking_calendar_connections
WHERE client_id = %(client_id)s AND user_id = %(user_id)s;
