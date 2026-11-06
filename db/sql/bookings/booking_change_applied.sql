-- A pending request to move a booking, once it has been applied: it is closed so it never shows
-- again. Its holds are freed by the caller, in the same transaction.
UPDATE public.bookings
SET status = 'cancelled', cancelled_at = now(), cancel_reason = 'change_applied',
    version = version + 1
WHERE client_id = %(client_id)s AND id = %(id)s;
