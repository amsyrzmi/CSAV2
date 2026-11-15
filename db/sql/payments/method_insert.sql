INSERT INTO public.payment_methods
    (client_id, kind, label, details, instructions, is_active, sort_order)
VALUES (%(client_id)s, %(kind)s, %(label)s, %(details)s, %(instructions)s, %(is_active)s,
        %(sort_order)s)
RETURNING id, kind, label, details, instructions, is_active, sort_order, version,
          (SELECT jsonb_build_object('id', a.id, 'version', a.sha256)
           FROM public.media_assets a
           WHERE a.client_id = payment_methods.client_id AND a.kind = 'payment_qr'
             AND a.subject_key = payment_methods.id::text) AS picture;
