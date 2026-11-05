SELECT subject_kind, subject_id, weekday, opens, closes
FROM public.booking_hours
WHERE client_id = %(client_id)s
ORDER BY subject_kind, subject_id, weekday, opens;
