-- The staff note of this turn's escalation, set once: a second call finds the note filled and
-- returns no row, so the caller records the knowledge gap only on the first.
UPDATE public.escalations
SET note = %s
WHERE client_id = %s
  AND trace_id = %s
  AND note IS NULL
RETURNING id;
