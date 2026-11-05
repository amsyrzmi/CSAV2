SELECT id, version FROM public.booking_types
WHERE client_id = %(client_id)s AND id = %(id)s
FOR UPDATE;
