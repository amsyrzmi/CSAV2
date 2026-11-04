DELETE FROM public.booking_holds
WHERE client_id = %(client_id)s AND booking_id = %(booking_id)s;
