INSERT INTO public.booking_type_resources (client_id, type_id, resource_id)
SELECT %(client_id)s, %(type_id)s, unnest(%(ids)s::uuid[]);
