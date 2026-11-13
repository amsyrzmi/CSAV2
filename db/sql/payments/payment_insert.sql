-- Appends one payment to the ledger for an invoice the caller has locked (invoice_lock.sql). The
-- amount and currency are copied from the invoice row in this statement, so they always equal what
-- was invoiced. payments is append-only for csa_app (SELECT, INSERT); payments_one_per_invoice and
-- payments_idempotency_uq refuse a second payment for the invoice or a reused key.
INSERT INTO public.payments
    (client_id, invoice_id, amount, currency, received_at, method_kind, source, matched_by,
     created_by, note, idempotency_key)
SELECT i.client_id, i.id, i.amount, i.currency, %(received_at)s, %(method_kind)s, %(source)s,
       %(matched_by)s, %(created_by)s, %(note)s, %(idempotency_key)s
FROM public.invoices i
WHERE i.client_id = %(client_id)s AND i.id = %(invoice_id)s
RETURNING id;
