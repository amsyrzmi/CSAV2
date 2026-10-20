INSERT INTO ecommerce_authorization_attempts (id, client_id, context, state_hash)
VALUES (%s, %s, %s, %s)
RETURNING id, context, status, created_at, expires_at;
