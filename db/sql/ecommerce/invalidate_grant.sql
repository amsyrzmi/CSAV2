UPDATE ecommerce_grants
SET state = %s, revision = revision + 1
WHERE client_id = %s AND id = %s AND revision = %s AND state = 'active'
RETURNING revision;
