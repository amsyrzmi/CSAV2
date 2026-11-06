-- The connected people on one booking: who gets an event, for the push right after it commits.
SELECT a.person_id
FROM public.booking_attendees a
JOIN public.booking_calendar_connections c
  ON c.client_id = a.client_id AND c.person_id = a.person_id
WHERE a.client_id = %(client_id)s AND a.booking_id = %(booking_id)s AND c.status = 'connected'
ORDER BY a.person_id;
