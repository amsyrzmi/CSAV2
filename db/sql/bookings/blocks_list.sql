SELECT id, subject_kind, subject_id, starts_at, ends_at, note, created_by
FROM public.booking_blocks
WHERE client_id = %(client_id)s
  AND ends_at > coalesce(%(from)s::timestamptz, now())
  AND (%(to)s::timestamptz IS NULL OR starts_at < %(to)s::timestamptz)
ORDER BY starts_at, id
LIMIT 500;
