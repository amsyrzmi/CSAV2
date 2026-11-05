SELECT id, display_name, user_id, employee_id, email, is_active, version
FROM public.booking_people
WHERE client_id = %(client_id)s
ORDER BY lower(display_name), id;
