DELETE FROM public.booking_type_resources
WHERE client_id = %(client_id)s AND type_id = %(type_id)s;
