INSERT INTO public.order_events (client_id, order_id, event, actor, payload)
VALUES (%s, %s, %s, %s, %s)
RETURNING id;
