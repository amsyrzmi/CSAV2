-- The subscription's newest open invoice (issued, overdue, proof_submitted or cash_pending: the
-- invoices_open list) for the staff detail view, or no row. Staff-only invoice fields (void
-- reason, voider) are not read.
SELECT id, reference, status, amount, currency, due_at
FROM public.invoices
WHERE client_id = %(client_id)s AND subscription_id = %(subscription_id)s
  AND status IN ('issued', 'overdue', 'proof_submitted', 'cash_pending')
ORDER BY issued_at DESC, id DESC
LIMIT 1;
