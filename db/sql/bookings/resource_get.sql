SELECT id, name, capacity, is_active, version
FROM public.booking_resources
WHERE client_id = %(client_id)s AND id = %(id)s
FOR UPDATE;
