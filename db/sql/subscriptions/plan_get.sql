SELECT code, name, price, currency, billing_interval, features, sort_order, is_active,
       signup_mode, is_signup_open
FROM public.plans
WHERE client_id = %(client_id)s AND code = %(code)s;
