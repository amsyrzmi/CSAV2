UPDATE public.bookings
SET status = %(status)s,
    cancelled_at = CASE WHEN %(status)s = 'cancelled' THEN now() ELSE cancelled_at END,
    version = version + 1
WHERE client_id = %(client_id)s AND id = %(id)s;
