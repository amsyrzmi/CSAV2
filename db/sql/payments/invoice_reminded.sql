-- The invoice was last reminded now (a chat message written or an email row queued). The caller
-- holds the invoice lock (invoice_lock.sql).
UPDATE public.invoices SET reminded_at = %(now)s
WHERE client_id = %(client_id)s AND id = %(invoice_id)s;
