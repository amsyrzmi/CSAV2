-- Step 4 of the payments_due job, first statement (only when payment_settings.suspend_after_days
-- is set): claims the active subscriptions with an overdue invoice due before %(cutoff)s (now
-- minus suspend_after_days), at most %(batch_size)s, FOR UPDATE OF s SKIP LOCKED, and returns their
-- ids. A subscription that staff are paying for holds its lock (mark paid locks the subscription
-- first) and is skipped. payments_due_suspend.sql then reads the invoices again in a statement of
-- its own, so a payment that committed before this lock was taken is seen there and the
-- subscription is not paused; a payment that comes later waits for this transaction and lifts the
-- pause itself (resumed_paid). Only invoices of active subscriptions count; a pending
-- subscription's opening invoice never suspends anything.
-- Suspension happens at most once per invoice: an overdue invoice that already caused a
-- 'suspended_unpaid' event on this subscription (payload invoice_id) no longer counts, so a staff
-- resume (or rule 3's) of an unpaid pause is not undone by the next run while that invoice stays
-- overdue. Another overdue invoice that has caused no suspension yet still counts.
-- payments_due_suspend.sql applies exactly the same invoice test.
SELECT s.id
FROM public.subscriptions s
WHERE s.client_id = %(client_id)s
  AND s.status = 'active'
  AND EXISTS (
      SELECT 1 FROM public.invoices i
      WHERE i.client_id = s.client_id AND i.subscription_id = s.id
        AND i.status = 'overdue' AND i.due_at < %(cutoff)s::timestamptz
        AND NOT EXISTS (
            SELECT 1 FROM public.subscription_events e
            WHERE e.client_id = s.client_id AND e.subscription_id = s.id
              AND e.action = 'suspended_unpaid' AND e.payload->>'invoice_id' = i.id))
ORDER BY s.id
FOR UPDATE OF s SKIP LOCKED
LIMIT %(batch_size)s;
