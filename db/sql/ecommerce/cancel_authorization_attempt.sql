UPDATE ecommerce_authorization_attempts
SET status = 'cancelled'
WHERE client_id = %s AND id = %s AND status = 'pending' AND expires_at > clock_timestamp()
RETURNING id;
