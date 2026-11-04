SELECT subject_kind, subject_id FROM public.booking_holds
WHERE client_id = %(client_id)s AND booking_id = %(booking_id)s
ORDER BY subject_kind, subject_id;
