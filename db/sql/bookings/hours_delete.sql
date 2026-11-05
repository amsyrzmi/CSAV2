DELETE FROM public.booking_hours
WHERE client_id = %(client_id)s AND subject_kind = %(kind)s
  AND subject_id IS NOT DISTINCT FROM %(subject_id)s;
