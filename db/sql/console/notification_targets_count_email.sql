-- How many email targets the company has (switched off ones count) and whether one already holds
-- the address. Named parameters: client_id, address (lowercased).
SELECT count(*)::int AS n,
       coalesce(bool_or(address = %(address)s), false) AS has_address
FROM public.staff_notification_targets
WHERE client_id = %(client_id)s AND channel = 'email';
