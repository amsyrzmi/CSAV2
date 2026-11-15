SELECT p.code, p.name, p.price, p.currency, p.billing_interval, p.features, p.sort_order,
       p.is_active, p.signup_mode, p.is_signup_open,
       CASE WHEN a.id IS NULL THEN NULL
            ELSE jsonb_build_object('id', a.id, 'version', a.sha256) END AS picture
FROM public.plans p
LEFT JOIN public.media_assets a
    ON a.client_id = p.client_id AND a.kind = 'plan' AND a.subject_key = p.code
WHERE p.client_id = %(client_id)s
  AND (%(include_archived)s OR p.is_active)
ORDER BY p.sort_order, p.code;
