SELECT user_id FROM public.booking_people
WHERE client_id = %(client_id)s AND user_id IS NOT NULL;
