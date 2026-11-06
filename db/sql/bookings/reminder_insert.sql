-- One reminder per booking, start time, audience and address. A row that already exists is left as
-- it is, a `sent` or `failed` one included, so nobody is reminded twice of one start. A `dropped`
-- row (the booking moved away, or the address went) is queued again: the booking is back at that
-- start. Returns a row only when something was queued, new or again. created_at is when it was
-- owed, by the job's clock when it gives one (a lapse notice's age is measured from it).
INSERT INTO public.booking_reminders
    (client_id, booking_id, starts_at, audience, channel, address, status, sent_at, created_at)
VALUES (%(client_id)s, %(booking_id)s, %(starts_at)s, %(audience)s, %(channel)s, %(address)s,
        %(status)s, %(sent_at)s, coalesce(%(created_at)s::timestamptz, now()))
ON CONFLICT ON CONSTRAINT booking_reminders_once DO UPDATE
   SET status = 'queued', sent_at = NULL, created_at = EXCLUDED.created_at
 WHERE public.booking_reminders.status = 'dropped'
RETURNING id;
