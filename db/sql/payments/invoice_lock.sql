-- Locks one invoice of the company until the transaction ends and reads it as invoice_get.sql.
-- LOCK ORDER (every payments function, the invoice job and the subscriptions executor): the
-- subscription row first (subscription_for_invoice.sql or subscriptions/subscription_lock.sql,
-- FOR UPDATE), then the invoice row (this file, or an UPDATE of invoices). Taking them in this one
-- order is what keeps mark paid, void, claim, void_for_subscription and the job from deadlocking.
-- Under READ COMMITTED a caller that waited here reads the row as the winner committed it.
SELECT i.id, i.client_id, i.contact_id, i.subscription_id, i.reference, i.status, i.amount,
       i.currency, i.issued_at, i.due_at, i.paid_at, i.period_start, i.period_end,
       i.line_items -> 0 ->> 'description' AS plan_name, i.claim_note, i.claimed_at,
       i.method_kind,
       EXISTS (SELECT 1 FROM public.payments p
                WHERE p.client_id = i.client_id AND p.invoice_id = i.id) AS has_payment
FROM public.invoices i
WHERE i.client_id = %(client_id)s AND i.id = %(invoice_id)s
FOR UPDATE OF i;
