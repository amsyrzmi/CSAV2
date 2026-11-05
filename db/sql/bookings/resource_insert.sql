INSERT INTO public.booking_resources (client_id, name, capacity)
VALUES (%(client_id)s, %(name)s, %(capacity)s)
RETURNING id, name, capacity, is_active, version;
