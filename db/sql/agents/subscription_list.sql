SELECT id, plan, status, price::text, currency, renews_at FROM subscriptions
WHERE client_id = %s AND contact_id = %s
ORDER BY started_at DESC LIMIT 50;
