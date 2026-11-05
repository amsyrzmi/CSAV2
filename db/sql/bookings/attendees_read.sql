SELECT a.booking_id, a.person_id, a.guest_name, a.guest_email,
       p.display_name, p.is_active, p.user_id, p.email, p.employee_id
FROM public.booking_attendees a
LEFT JOIN public.booking_people p ON p.client_id = a.client_id AND p.id = a.person_id
WHERE a.client_id = %(client_id)s AND a.booking_id = ANY(%(ids)s)
ORDER BY lower(p.display_name), a.id;
