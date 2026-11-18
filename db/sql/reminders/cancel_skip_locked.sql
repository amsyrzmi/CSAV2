-- Cancel the scheduled reminders of these kinds for one subject (every cycle) without waiting for a
-- row lock: a row another transaction holds (the fire job firing it, claim_due.sql FOR UPDATE, or a
-- schedule moving it) is skipped and stays as it is. Rows whose dedupe_key is in %(keep)s are left
-- alone (the caller schedules them again; a cancelled row is never revived by schedule.sql).
-- Fired, cancelled and done rows are history. Returns one row per reminder cancelled.
-- Read along reminders (client_id, kind, subject_type, subject_id).
UPDATE public.reminders r
SET state = 'cancelled', updated_at = now()
WHERE r.client_id = %(client_id)s
  AND r.state = 'scheduled'
  AND r.id IN (
      SELECT x.id
      FROM public.reminders x
      WHERE x.client_id = %(client_id)s
        AND x.kind = ANY(%(kinds)s::text[])
        AND x.subject_type = %(subject_type)s
        AND x.subject_id = %(subject_id)s
        AND x.state = 'scheduled'
        AND NOT (x.dedupe_key = ANY(%(keep)s::text[]))
      FOR UPDATE SKIP LOCKED
  )
RETURNING r.id;
