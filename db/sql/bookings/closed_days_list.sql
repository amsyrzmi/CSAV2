SELECT day, note FROM public.booking_closed_days
WHERE client_id = %(client_id)s
  AND (%(from)s::date IS NULL OR day >= %(from)s::date)
  AND (%(to)s::date IS NULL OR day <= %(to)s::date)
ORDER BY day;
