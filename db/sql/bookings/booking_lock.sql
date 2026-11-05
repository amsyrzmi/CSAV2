SELECT id FROM public.bookings WHERE client_id = %(client_id)s AND id = %(id)s FOR UPDATE;
