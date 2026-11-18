-- Cancel the scheduled reminders of one kind for one subject (every cycle). Fired, cancelled and
-- done rows are history and stay as they are. Returns one row per reminder cancelled.
UPDATE public.reminders
SET state = 'cancelled', updated_at = now()
WHERE client_id = %(client_id)s AND kind = %(kind)s
  AND subject_type = %(subject_type)s AND subject_id = %(subject_id)s
  AND state = 'scheduled'
RETURNING id;
