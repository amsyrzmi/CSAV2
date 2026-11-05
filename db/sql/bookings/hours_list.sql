SELECT weekday, to_char(opens, 'HH24:MI') AS opens, to_char(closes, 'HH24:MI') AS closes
FROM public.booking_hours
WHERE client_id = %(client_id)s AND subject_kind = %(kind)s
  AND subject_id IS NOT DISTINCT FROM %(subject_id)s
ORDER BY weekday, booking_hours.opens;
