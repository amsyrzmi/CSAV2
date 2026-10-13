INSERT INTO public.tickets
    (client_id, conversation_id, contact_id, subject, category, priority, sla_due_at)
VALUES (%s, %s, %s, %s, %s, %s, now() + make_interval(hours => %s))
RETURNING id, status, priority, sla_due_at;
