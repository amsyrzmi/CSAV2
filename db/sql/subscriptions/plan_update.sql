UPDATE public.plans
SET name = coalesce(%(name)s, name),
    price = coalesce(%(price)s, price),
    billing_interval = coalesce(%(billing_interval)s, billing_interval),
    features = coalesce(%(features)s::jsonb, features),
    sort_order = coalesce(%(sort_order)s, sort_order),
    is_active = coalesce(%(is_active)s, is_active),
    signup_mode = coalesce(%(signup_mode)s, signup_mode),
    is_signup_open = coalesce(%(is_signup_open)s, is_signup_open)
WHERE client_id = %(client_id)s AND code = %(code)s
RETURNING code, name, price, currency, billing_interval, features, sort_order, is_active,
          signup_mode, is_signup_open,
          (SELECT jsonb_build_object('id', a.id, 'version', a.sha256)
           FROM public.media_assets a
           WHERE a.client_id = plans.client_id AND a.kind = 'plan'
             AND a.subject_key = plans.code) AS picture;
