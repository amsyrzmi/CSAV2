-- Claim queued email rows, oldest first, across every company (the mail job runs as the owner).
-- They are `sending` once this commits, so no other run and no later run picks them up again,
-- even if this worker dies before it settles them.
UPDATE public.booking_reminders r
   SET status = 'sending'
  FROM (SELECT client_id, id
        FROM public.booking_reminders
        WHERE channel = 'email' AND status = 'queued'
        ORDER BY created_at, id
        LIMIT %(batch_size)s
        FOR UPDATE SKIP LOCKED) due
 WHERE r.client_id = due.client_id AND r.id = due.id
RETURNING r.client_id, r.id, r.booking_id, r.starts_at, r.audience, r.address;
