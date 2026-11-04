SELECT h.booking_id, r.id, r.name, r.is_active
FROM public.booking_holds h
JOIN public.booking_resources r ON r.client_id = h.client_id AND r.id = h.subject_id
WHERE h.client_id = %(client_id)s AND h.subject_kind = 'resource'
  AND h.booking_id = ANY(%(ids)s)
ORDER BY lower(r.name), r.id;
