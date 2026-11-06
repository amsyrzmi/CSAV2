-- A person's connection with its encrypted tokens, for the sync code only.
SELECT id, user_id, person_id, status, calendar_id, access_token, refresh_token, token_expires_at
FROM public.booking_calendar_connections
WHERE client_id = %(client_id)s AND person_id = %(person_id)s;
