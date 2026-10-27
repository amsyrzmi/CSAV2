INSERT INTO public.subscription_events
    (client_id, subscription_id, actor, action, from_status, to_status, payload, proposal_id)
VALUES (%(client_id)s, %(subscription_id)s, %(actor)s, %(action)s, %(from_status)s,
        %(to_status)s, %(payload)s, %(proposal_id)s);
