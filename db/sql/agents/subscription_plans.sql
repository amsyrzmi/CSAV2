SELECT code, name, price, currency, billing_interval, features FROM public.plans
WHERE client_id = %(client_id)s AND is_active
ORDER BY sort_order, code LIMIT 50;
