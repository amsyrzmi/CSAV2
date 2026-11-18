-- Claim the company's oldest due reminder for this run: scheduled, due at or before the job's
-- time (%(now)s, so a test clock decides due-ness), not one this run has already handled
-- (%(seen)s), and not one another run holds (SKIP LOCKED: two workers never fire the same row).
-- The lock lasts until the claim's transaction commits the decision.
SELECT id, kind, subject_type, subject_id, due_at, fire_count, attempts, payload
FROM public.reminders
WHERE client_id = %(client_id)s
  AND state = 'scheduled'
  AND due_at <= %(now)s
  AND NOT (id = ANY(%(seen)s::uuid[]))
ORDER BY due_at, id
LIMIT 1
FOR UPDATE SKIP LOCKED;
