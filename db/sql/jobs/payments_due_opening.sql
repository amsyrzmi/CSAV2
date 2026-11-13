-- Step 1 of the payments_due job: claims the pending subscriptions (approved, not started) that
-- have no opening invoice yet, in any status (a void one included: invoices_subscription_period_uq
-- allows one invoice with a NULL period_start per subscription), oldest approval first, at most
-- %(batch_size)s, FOR UPDATE OF s SKIP LOCKED, and returns their ids. The job then calls
-- csa.payments.invoices.issue_opening_invoice for each. A zero price is left out here because that
-- function issues nothing for it, so such rows never take the batch from later ones. Only the
-- subscription is locked: the payments lock order (subscription, then invoice).
SELECT s.id
FROM public.subscriptions s
WHERE s.client_id = %(client_id)s
  AND s.status = 'pending'
  AND s.price > 0
  AND NOT EXISTS (
      SELECT 1 FROM public.invoices i
      WHERE i.client_id = s.client_id AND i.subscription_id = s.id AND i.period_start IS NULL)
ORDER BY s.started_at, s.id
FOR UPDATE OF s SKIP LOCKED
LIMIT %(batch_size)s;
