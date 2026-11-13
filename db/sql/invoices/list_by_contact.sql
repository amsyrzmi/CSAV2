-- The contact's own invoices as a customer may see them, newest first: never a draft or a void
-- one, and a 'paid' one only when the ledger holds its payment (a 'paid' row without one is an old
-- seed).
SELECT i.id, i.client_id, i.contact_id, i.order_id, i.status, i.amount, i.currency, i.issued_at,
       i.due_at, i.paid_at, i.payment_ref, i.line_items
FROM public.invoices i
WHERE i.client_id = %s
  AND i.contact_id = %s
  AND i.status IN ('issued', 'overdue', 'proof_submitted', 'cash_pending', 'paid')
  AND (i.status <> 'paid'
       OR EXISTS (SELECT 1 FROM public.payments p
                   WHERE p.client_id = i.client_id AND p.invoice_id = i.id))
ORDER BY i.issued_at DESC
LIMIT %s;
