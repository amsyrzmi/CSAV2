-- Record a failed fire on the claimed reminder: the attempt count and the error's class, then
-- either a retry at %(due_at)s (state stays 'scheduled') or, on the last attempt, state 'fired' so
-- it never runs again. fire_count is untouched: nothing was delivered.
UPDATE public.reminders
SET state = %(state)s,
    due_at = %(due_at)s,
    attempts = %(attempts)s,
    last_error = %(last_error)s,
    updated_at = now()
WHERE client_id = %(client_id)s AND id = %(id)s;
