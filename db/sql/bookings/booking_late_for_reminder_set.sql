-- A booking the customer's bot asked for, confirmed by staff after its reminder was due: the
-- confirmation is the reminder. Only ever turned on here; a later move sets it again.
UPDATE public.bookings
SET late_for_reminder = true
WHERE client_id = %(client_id)s AND id = %(id)s;
