INSERT INTO public.ticket_events (client_id, ticket_id, actor, event_type, old_value, new_value)
VALUES (%s, %s, %s, %s, %s, %s)
RETURNING id;
