SELECT id, context, status, created_at, expires_at
FROM ecommerce_authorization_attempts
WHERE client_id = %s AND id = %s AND state_hash = %s;
