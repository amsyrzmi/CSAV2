DELETE FROM public.booking_blocks
WHERE client_id = %(client_id)s AND id = %(id)s
RETURNING id;
