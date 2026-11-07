-- The connected people on one booking: who gets an event, for the push right after it commits.
-- Only while the member is enabled, staff or an admin, and still linked to that active person.
SELECT a.person_id
FROM public.booking_attendees a
JOIN public.booking_calendar_connections c
  ON c.client_id = a.client_id AND c.person_id = a.person_id AND c.status = 'connected'
JOIN public.booking_people p ON p.client_id = c.client_id AND p.id = c.person_id
                             AND p.user_id = c.user_id AND p.is_active
WHERE a.client_id = %(client_id)s AND a.booking_id = %(booking_id)s
  AND public.booking_member_active(c.client_id, c.user_id)
ORDER BY a.person_id;
