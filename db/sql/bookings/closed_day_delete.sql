DELETE FROM public.booking_closed_days
WHERE client_id = %(client_id)s AND day = %(day)s
RETURNING day;
