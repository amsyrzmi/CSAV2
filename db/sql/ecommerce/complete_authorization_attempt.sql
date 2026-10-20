UPDATE ecommerce_authorization_attempts
SET status = 'completed', grant_id = %s
WHERE client_id = %s AND id = %s AND status = 'consumed' AND expires_at > clock_timestamp()
RETURNING id;
