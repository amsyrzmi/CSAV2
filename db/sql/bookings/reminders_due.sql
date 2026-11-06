-- Confirmed bookings that start after now and within the reminder window, earliest first, a page at
-- a time: the next page asks for the rows after the last one it saw, so the same few bookings
-- never fill every run. A pending booking is never here, and neither is one that has started, nor
-- one whose start was set after its reminder was due (late_for_reminder).
SELECT id, starts_at
FROM public.bookings
WHERE client_id = %(client_id)s AND status = 'confirmed' AND NOT late_for_reminder
  AND starts_at > %(now)s AND starts_at <= %(until)s
  AND (starts_at, id) > (coalesce(%(after_starts)s::timestamptz, '-infinity'::timestamptz),
                         coalesce(%(after_id)s::uuid, '00000000-0000-0000-0000-000000000000'::uuid))
ORDER BY starts_at, id
LIMIT %(batch_size)s;
