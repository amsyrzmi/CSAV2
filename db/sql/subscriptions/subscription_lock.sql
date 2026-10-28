-- Locks one subscription for a subscription_action executor run; the caller compares version
-- with the proposal's bound version before applying anything.
SELECT id, plan, status, price, currency, renews_at, pending_plan, pending_effective_at,
       cancel_at_period_end, version
FROM public.subscriptions
WHERE client_id = %(client_id)s AND id = %(subscription_id)s
FOR UPDATE;
