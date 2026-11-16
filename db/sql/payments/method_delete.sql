-- Removes one method of one company. The caller holds the payments lock and has just read the
-- method in the same transaction, so a second delete waits on the lock and then reads no row. No
-- row back here can only mean a path that skips the lock removed it; the caller answers 404 as a
-- defence. Invoices and payments keep only `method_kind`, so nothing else points at this row.
DELETE FROM public.payment_methods
WHERE client_id = %(client_id)s AND id = %(method_id)s
RETURNING id;
