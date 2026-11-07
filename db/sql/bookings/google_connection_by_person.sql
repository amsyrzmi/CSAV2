-- A person's connection with its encrypted tokens, for the sync code only. None while the member
-- is disabled, no longer staff or an admin, or no longer the account linked to that person.
SELECT c.id, c.user_id, c.person_id, c.status, c.calendar_id, c.access_token, c.refresh_token,
       c.token_expires_at
FROM public.booking_calendar_connections c
JOIN public.booking_people p ON p.client_id = c.client_id AND p.id = c.person_id
                             AND p.user_id = c.user_id AND p.is_active
WHERE c.client_id = %(client_id)s AND c.person_id = %(person_id)s
  AND public.booking_member_active(c.client_id, c.user_id);
