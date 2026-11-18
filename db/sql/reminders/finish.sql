-- Write a handler's decision on the claimed reminder. state is 'scheduled' (fire again at
-- %(due_at)s) or 'done' (finished or dropped). %(fired)s is 1 when the handler acted (fire_count
-- and last_fired_at move) and 0 when it was dropped. A success clears the failure count and error.
UPDATE public.reminders
SET state = %(state)s,
    due_at = coalesce(%(due_at)s::timestamptz, due_at),
    fire_count = fire_count + %(fired)s::int,
    last_fired_at = CASE WHEN %(fired)s::int > 0 THEN %(now)s::timestamptz ELSE last_fired_at END,
    attempts = 0,
    last_error = %(last_error)s::text,
    updated_at = now()
WHERE client_id = %(client_id)s AND id = %(id)s;
