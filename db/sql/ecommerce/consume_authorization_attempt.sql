UPDATE ecommerce_authorization_attempts
SET status = 'consumed', consumed_at = clock_timestamp()
WHERE client_id = %s AND id = %s AND state_hash = %s
  AND status = 'pending' AND expires_at > clock_timestamp()
RETURNING id;
