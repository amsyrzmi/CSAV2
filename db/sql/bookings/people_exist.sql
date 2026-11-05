SELECT id FROM public.booking_people
WHERE client_id = %(client_id)s AND id = ANY(%(ids)s);
