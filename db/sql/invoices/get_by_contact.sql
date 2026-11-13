-- One of the contact's own invoices, as a customer may see it: never a draft or a void one, and a
-- 'paid' one only when the ledger holds its payment (a 'paid' row without one is an old seed).
SELECT i.id,
       i.client_id,
       i.contact_id,
       i.order_id,
       i.status,
       i.amount,
       i.currency,
       i.issued_at,
       i.due_at,
       i.paid_at,
       i.payment_ref,
       i.line_items,
       c.email,
       c.telegram_id,
       c.full_name
FROM public.invoices i
JOIN public.contacts c ON c.id = i.contact_id
WHERE i.client_id = %s
  AND (i.id = %s OR i.order_id = %s)
  AND i.contact_id = %s
  AND i.status IN ('issued', 'overdue', 'proof_submitted', 'cash_pending', 'paid')
  AND (i.status <> 'paid'
       OR EXISTS (SELECT 1 FROM public.payments p
                   WHERE p.client_id = i.client_id AND p.invoice_id = i.id))
LIMIT 1;
