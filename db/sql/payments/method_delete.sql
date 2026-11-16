-- Removes one method of one company. The caller holds the payments lock and has read the method
-- in the same transaction; no row back means another delete got there first (the caller answers
-- 404). Invoices and payments keep only `method_kind`, so nothing else points at this row.
DELETE FROM public.payment_methods
WHERE client_id = %(client_id)s AND id = %(method_id)s
RETURNING id;
