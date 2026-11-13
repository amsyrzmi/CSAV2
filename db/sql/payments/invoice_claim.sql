-- A customer says they paid their own locked issued or overdue invoice: it waits for staff as
-- proof_submitted with the customer's note (trimmed, at most 300 characters) and the time.
UPDATE public.invoices
SET status = 'proof_submitted',
    claim_note = %(note)s,
    claimed_at = now()
WHERE client_id = %(client_id)s AND id = %(invoice_id)s AND contact_id = %(contact_id)s
  AND status IN ('issued', 'overdue')
RETURNING id;
