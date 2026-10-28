SELECT code, name, price, currency, billing_interval, features, sort_order, is_active
FROM public.plans
WHERE client_id = %(client_id)s
  AND (%(include_archived)s OR is_active)
ORDER BY sort_order, code;
