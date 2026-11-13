INSERT INTO public.payment_methods
    (client_id, kind, label, details, instructions, is_active, sort_order)
VALUES (%(client_id)s, %(kind)s, %(label)s, %(details)s, %(instructions)s, %(is_active)s,
        %(sort_order)s)
RETURNING id, kind, label, details, instructions, is_active, sort_order, version;
