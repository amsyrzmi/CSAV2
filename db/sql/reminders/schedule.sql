-- Schedule a reminder, or move the one with the same (client_id, dedupe_key). An existing row has
-- its due_at and payload replaced only while it is still scheduled and has not fired
-- (fire_count = 0); a fired, cancelled or done row, or one waiting for a repeat, is left alone.
-- Either way the row's id comes back.
WITH put AS (
    INSERT INTO public.reminders
        (client_id, kind, subject_type, subject_id, due_at, payload, dedupe_key)
    VALUES (%(client_id)s, %(kind)s, %(subject_type)s, %(subject_id)s, %(due_at)s,
            %(payload)s, %(dedupe_key)s)
    ON CONFLICT (client_id, dedupe_key) DO UPDATE
    SET due_at = EXCLUDED.due_at, payload = EXCLUDED.payload, updated_at = now()
    WHERE public.reminders.state = 'scheduled' AND public.reminders.fire_count = 0
    RETURNING id
)
SELECT id FROM put
UNION ALL
SELECT r.id FROM public.reminders r
WHERE r.client_id = %(client_id)s AND r.dedupe_key = %(dedupe_key)s
  AND NOT EXISTS (SELECT 1 FROM put)
LIMIT 1;
