-- Settle as `failed` the company's email rows still `sending` that were queued before
-- %(lost_before)s: a run claimed them and never finished (its worker stopped mid-send, or its
-- timeout cut it off), so whether they went is unknown. They are never queued again: a reminder is
-- sent at most once. Run in the company's own transaction, before any row of this run is claimed,
-- while the job's lock keeps any other run from sending.
UPDATE public.booking_reminders
   SET status = 'failed'
 WHERE client_id = %(client_id)s AND channel = 'email' AND status = 'sending'
   AND created_at < %(lost_before)s
RETURNING id;
