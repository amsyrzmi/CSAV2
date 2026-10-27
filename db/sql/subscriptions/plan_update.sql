UPDATE public.plans
SET name = coalesce(%(name)s, name),
    price = coalesce(%(price)s, price),
    billing_interval = coalesce(%(billing_interval)s, billing_interval),
    features = coalesce(%(features)s::jsonb, features),
    sort_order = coalesce(%(sort_order)s, sort_order),
    is_active = coalesce(%(is_active)s, is_active)
WHERE client_id = %(client_id)s AND code = %(code)s
RETURNING code, name, price, currency, billing_interval, features, sort_order, is_active;
