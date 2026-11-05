INSERT INTO public.booking_type_people (client_id, type_id, person_id)
SELECT %(client_id)s, %(type_id)s, unnest(%(ids)s::uuid[]);
