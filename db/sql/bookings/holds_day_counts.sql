-- How many bookings each person or room holds that start inside the window.
SELECT h.subject_id, count(*) AS n
FROM public.booking_holds h
WHERE h.client_id = %(client_id)s
  AND h.booking_id IS NOT NULL
  AND h.subject_kind = %(kind)s
  AND h.subject_id = ANY(%(ids)s)
  AND lower(h.during) >= %(from)s
  AND lower(h.during) < %(to)s
GROUP BY h.subject_id;
