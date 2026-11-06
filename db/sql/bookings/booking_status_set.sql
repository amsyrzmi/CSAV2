UPDATE public.bookings
SET status = %(status)s,
    cancelled_at = CASE WHEN %(status)s = 'cancelled' THEN now() ELSE cancelled_at END,
    -- A booking cancelled while still pending was never confirmed: a customer was never told of
    -- one that staff made (see agents/booking_get.sql).
    cancel_reason = CASE WHEN %(status)s = 'cancelled' AND status = 'pending'
                         THEN 'never_confirmed' ELSE cancel_reason END,
    version = version + 1
WHERE client_id = %(client_id)s AND id = %(id)s;
