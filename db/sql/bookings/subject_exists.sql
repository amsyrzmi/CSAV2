SELECT (
    (%(kind)s = 'person' AND EXISTS (
        SELECT 1 FROM public.booking_people WHERE client_id = %(client_id)s AND id = %(id)s))
    OR (%(kind)s = 'resource' AND EXISTS (
        SELECT 1 FROM public.booking_resources WHERE client_id = %(client_id)s AND id = %(id)s))
) AS found;
