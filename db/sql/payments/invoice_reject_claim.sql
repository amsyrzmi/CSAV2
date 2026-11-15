-- Staff reject a customer's "I have paid" claim on one locked invoice: it goes back to issued, so the
-- overdue marking, the reminders and the pending-subscription expiry see it again. claim_note and
-- claimed_at stay as the record of what the customer said; a second claim overwrites them.
UPDATE public.invoices
SET status = 'issued'
WHERE client_id = %(client_id)s AND id = %(invoice_id)s
  AND status = 'proof_submitted'
RETURNING id;
