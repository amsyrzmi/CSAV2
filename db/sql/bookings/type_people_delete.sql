DELETE FROM public.booking_type_people
WHERE client_id = %(client_id)s AND type_id = %(type_id)s;
