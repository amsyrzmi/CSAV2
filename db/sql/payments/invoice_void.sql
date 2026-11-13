-- Staff void one locked invoice that is not paid or void yet. void_reason, voided_at and voided_by
-- are staff-only fields (never shown to a customer or an agent).
UPDATE public.invoices
SET status = 'void',
    void_reason = %(reason)s,
    voided_at = now(),
    voided_by = %(voided_by)s
WHERE client_id = %(client_id)s AND id = %(invoice_id)s
  AND status IN ('draft', 'issued', 'overdue', 'proof_submitted', 'cash_pending')
RETURNING id;
