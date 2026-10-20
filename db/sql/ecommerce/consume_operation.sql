UPDATE ecommerce_read_operations SET consumed_at = clock_timestamp()
WHERE client_id = %s AND id = %s AND consumed_at IS NULL
RETURNING id;
