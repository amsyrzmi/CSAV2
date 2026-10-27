INSERT INTO public.plans
    (client_id, code, name, price, currency, billing_interval, features, sort_order)
VALUES (%(client_id)s, %(code)s, %(name)s, %(price)s, %(currency)s, %(billing_interval)s,
        %(features)s, %(sort_order)s)
ON CONFLICT (client_id, code) DO NOTHING
RETURNING code, name, price, currency, billing_interval, features, sort_order, is_active;
