-- One invoice of the company as InvoiceRecord reads it, without a lock. mark_paid, void_invoice
-- and record_claim read it first only to learn the invoice's subscription_id (never changed once
-- set), so they can take the subscription lock before the invoice lock (see invoice_lock.sql).
-- has_payment: whether the ledger holds this invoice's payment. A 'paid' row without one is an
-- old seed and is never presented as paid. plan_name is the first line item's description, as it
-- was when the invoice was issued (it does not follow later plan edits).
SELECT i.id, i.client_id, i.contact_id, i.subscription_id, i.reference, i.status, i.amount,
       i.currency, i.issued_at, i.due_at, i.paid_at, i.period_start, i.period_end,
       i.line_items -> 0 ->> 'description' AS plan_name, i.claim_note, i.claimed_at,
       i.method_kind,
       EXISTS (SELECT 1 FROM public.payments p
                WHERE p.client_id = i.client_id AND p.invoice_id = i.id) AS has_payment
FROM public.invoices i
WHERE i.client_id = %(client_id)s AND i.id = %(invoice_id)s;
