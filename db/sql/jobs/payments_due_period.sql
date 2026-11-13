-- Step 2 of the payments_due job: claims the active subscriptions, not cancelling at period end,
-- whose next period starts (renews_at) after %(now)s and no later than %(horizon)s (now plus the
-- company's lead_days), with no invoice for period_start = renews_at in any status, earliest
-- period first, at most %(batch_size)s, FOR UPDATE OF s SKIP LOCKED, and returns their ids. The
-- job then calls csa.payments.invoices.issue_period_invoice for each.
-- A period that has already started (renews_at <= now, a renewal the subscriptions job has not
-- made yet) is never invoiced, so switching payments on bills no running period. The same price
-- test as payments/subscription_for_invoice.sql leaves out a period that would cost nothing (the
-- pending plan when pending_effective_at <= renews_at and it is still active, at its current
-- price, else the subscription's price): issue_period_invoice issues nothing for it, and such rows
-- must not take the batch from later ones. Only the subscription is locked (OF s): the payments
-- lock order (subscription, then invoice).
SELECT s.id
FROM public.subscriptions s
LEFT JOIN public.plans np
    ON np.client_id = s.client_id AND np.code = s.pending_plan AND np.is_active
   AND s.pending_effective_at <= s.renews_at
WHERE s.client_id = %(client_id)s
  AND s.status = 'active'
  AND NOT s.cancel_at_period_end
  AND s.renews_at > %(now)s::timestamptz
  AND s.renews_at <= %(horizon)s::timestamptz
  AND CASE WHEN np.code IS NOT NULL THEN np.price ELSE s.price END > 0
  AND NOT EXISTS (
      SELECT 1 FROM public.invoices i
      WHERE i.client_id = s.client_id AND i.subscription_id = s.id
        AND i.period_start = s.renews_at)
ORDER BY s.renews_at, s.id
FOR UPDATE OF s SKIP LOCKED
LIMIT %(batch_size)s;
