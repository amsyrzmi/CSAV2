UPDATE public.bookings
SET starts_at = %(starts_at)s, ends_at = %(ends_at)s, version = version + 1
WHERE client_id = %(client_id)s AND id = %(id)s;
