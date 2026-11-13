-- After a payment starts a pending subscription: its opening invoice (the one with no
-- period_start; invoices_subscription_period_uq allows only one) covers the first period, from the
-- new started_at to the new renews_at. The caller holds the subscription lock.
UPDATE public.invoices i
SET period_start = s.started_at,
    period_end = s.renews_at
FROM public.subscriptions s
WHERE i.client_id = %(client_id)s AND i.subscription_id = %(subscription_id)s
  AND i.period_start IS NULL
  AND s.client_id = i.client_id AND s.id = i.subscription_id
  AND s.renews_at > s.started_at
RETURNING i.id;
