-- Marks a locked, open invoice paid. It matches only when the payment %(payment_id)s for this very
-- invoice exists, i.e. was inserted earlier in this transaction (payment_insert.sql): an invoice is
-- never paid without its ledger row. paid_at is when the money was received; method_kind is kept
-- when none is given. other_overdue counts the subscription's other overdue invoices (an unpaid
-- pause is lifted only when there are none).
UPDATE public.invoices i
SET status = 'paid',
    paid_at = %(received_at)s::timestamptz,
    method_kind = coalesce(%(method_kind)s::text, i.method_kind)
WHERE i.client_id = %(client_id)s AND i.id = %(invoice_id)s
  AND i.status IN ('issued', 'overdue', 'proof_submitted', 'cash_pending')
  AND EXISTS (SELECT 1 FROM public.payments p
               WHERE p.client_id = i.client_id AND p.invoice_id = i.id
                 AND p.id = %(payment_id)s)
RETURNING i.id,
          (SELECT count(*) FROM public.invoices o
            WHERE o.client_id = i.client_id AND o.subscription_id = i.subscription_id
              AND o.status = 'overdue' AND o.id <> i.id) AS other_overdue;
