INSERT INTO public.booking_people (client_id, display_name, user_id, employee_id, email)
VALUES (%(client_id)s, %(display_name)s, %(user_id)s, %(employee_id)s, %(email)s)
RETURNING id, display_name, user_id, employee_id, email, is_active, version;
