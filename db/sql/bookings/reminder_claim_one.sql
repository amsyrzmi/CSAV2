-- Claim one queued email row as `sending`. Nothing is returned when it is no longer queued (another
-- run took it, or it was claimed already), so a row is never taken twice. Run in the company's own
-- transaction: row security hides every other company's rows.
UPDATE public.booking_reminders
   SET status = 'sending'
 WHERE client_id = %(client_id)s AND id = %(id)s AND channel = 'email' AND status = 'queued'
RETURNING client_id, id, booking_id, starts_at, audience, address, created_at;
