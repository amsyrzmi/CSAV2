INSERT INTO public.plans
    (client_id, code, name, price, currency, billing_interval, features, sort_order,
     signup_mode, is_signup_open)
VALUES (%(client_id)s, %(code)s, %(name)s, %(price)s, %(currency)s, %(billing_interval)s,
        %(features)s, %(sort_order)s, %(signup_mode)s, %(is_signup_open)s)
ON CONFLICT (client_id, code) DO NOTHING
RETURNING code, name, price, currency, billing_interval, features, sort_order, is_active,
          signup_mode, is_signup_open,
          (SELECT jsonb_build_object('id', a.id, 'version', a.sha256)
           FROM public.media_assets a
           WHERE a.client_id = plans.client_id AND a.kind = 'plan'
             AND a.subject_key = plans.code) AS picture;
