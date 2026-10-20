SELECT operation, request, grant_id, consumed_at FROM ecommerce_read_operations
WHERE client_id = %s AND id = %s;
