SELECT code, name, price::text, currency, billing_interval FROM plans
WHERE client_id = %s AND is_active
ORDER BY sort_order, code LIMIT 50;
