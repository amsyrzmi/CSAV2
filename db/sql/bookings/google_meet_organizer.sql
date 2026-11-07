-- Who asks Google for a booking's Meet link: the connected attendee who made the booking, else the
-- first connected attendee. One person, so the booking gets one link however many events it has.
SELECT a.person_id
FROM public.booking_attendees a
JOIN public.booking_calendar_connections c
  ON c.client_id = a.client_id AND c.person_id = a.person_id AND c.status = 'connected'
JOIN public.bookings b ON b.client_id = a.client_id AND b.id = a.booking_id
JOIN public.booking_people p ON p.client_id = a.client_id AND p.id = a.person_id
                            AND p.user_id = c.user_id AND p.is_active
WHERE a.client_id = %(client_id)s AND a.booking_id = %(booking_id)s
  AND public.booking_member_active(c.client_id, c.user_id)
ORDER BY (p.user_id = b.created_by) DESC, a.person_id
LIMIT 1;
