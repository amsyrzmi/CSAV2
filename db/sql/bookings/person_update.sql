UPDATE public.booking_people
SET display_name = %(display_name)s, user_id = %(user_id)s, employee_id = %(employee_id)s,
    email = %(email)s, is_active = %(is_active)s, version = version + 1
WHERE client_id = %(client_id)s AND id = %(id)s
RETURNING id, display_name, user_id, employee_id, email, is_active, version;
