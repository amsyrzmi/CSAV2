SELECT m.id, m.kind, m.label, m.details, m.instructions, m.is_active, m.sort_order, m.version,
       CASE WHEN a.id IS NULL THEN NULL
            ELSE jsonb_build_object('id', a.id, 'version', a.sha256) END AS picture
FROM public.payment_methods m
LEFT JOIN public.media_assets a
    ON a.client_id = m.client_id AND a.kind = 'payment_qr' AND a.subject_key = m.id::text
WHERE m.client_id = %(client_id)s AND m.id = %(method_id)s;
