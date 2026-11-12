-- An active plan of this company, by the code or the name the customer used.
SELECT code, name, is_signup_open FROM public.plans
WHERE client_id = %(client_id)s AND is_active
  AND (code = lower(%(plan)s) OR lower(name) = lower(%(plan)s))
ORDER BY (code = lower(%(plan)s)) DESC, sort_order, code
LIMIT 1;
