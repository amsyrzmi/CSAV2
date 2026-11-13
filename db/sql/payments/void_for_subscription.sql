-- A subscription is cancelled or expires: its open invoices for periods that will not run are
-- voided, the opening invoice (no period_start) included; a running period's invoice (period_start
-- before %(from_period)s), paid invoices and drafts stay. The caller holds the subscription lock
-- (subscriptions/subscription_lock.sql) first, then this UPDATE locks the invoices: the payments
-- lock order. voided_by stays NULL (the system voided it).
UPDATE public.invoices
SET status = 'void',
    void_reason = %(reason)s,
    voided_at = now()
WHERE client_id = %(client_id)s AND subscription_id = %(subscription_id)s
  AND status IN ('issued', 'overdue', 'proof_submitted', 'cash_pending')
  AND (period_start IS NULL OR period_start >= %(from_period)s::timestamptz)
RETURNING id;
