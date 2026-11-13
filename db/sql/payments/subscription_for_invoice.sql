-- Locks one subscription of the company (FOR UPDATE, until the transaction ends) and reads what an
-- invoice for it needs. LOCK ORDER: this lock comes BEFORE any invoice lock (invoice_lock.sql).
-- The current terms: price, currency and plan_name (the plan's name, else its code).
-- The terms of the period that starts at renews_at (next_*), as rule 2 of the subscriptions_due job
-- will apply them: the pending plan when pending_effective_at <= renews_at and that plan is still
-- active, at its CURRENT price and currency; otherwise the current terms. next_period_end is the
-- end of that period: public.subscription_next_renewal(anchor, renews_at, interval of the plan in
-- effect, clients.timezone), where the anchor is coalesce(renewal_anchor, renews_at), or renews_at
-- itself when the pending plan's billing interval differs from the current plan's (rule 2 resets
-- the anchor to renews_at then; a missing plan row counts as monthly). NULL without a renews_at.
WITH tenant AS (
    SELECT timezone FROM public.clients WHERE id = %(client_id)s
), terms AS (
    SELECT s.id, s.contact_id, s.status, s.plan, s.price, s.currency, s.renews_at,
           s.renewal_anchor, s.cancel_at_period_end, s.pause_reason, s.version,
           coalesce(cp.name, s.plan) AS plan_name,
           coalesce(cp.billing_interval, 'month') AS plan_interval,
           np.code AS next_code, np.name AS next_name, np.price AS next_plan_price,
           np.currency AS next_plan_currency, np.billing_interval AS next_plan_interval
    FROM public.subscriptions s
    LEFT JOIN public.plans cp ON cp.client_id = s.client_id AND cp.code = s.plan
    LEFT JOIN public.plans np
        ON np.client_id = s.client_id AND np.code = s.pending_plan AND np.is_active
       AND s.pending_effective_at <= s.renews_at
    WHERE s.client_id = %(client_id)s AND s.id = %(subscription_id)s
    FOR UPDATE OF s
)
SELECT t.id, t.contact_id, t.status, t.plan, t.price, t.currency, t.renews_at,
       t.renewal_anchor, t.cancel_at_period_end, t.pause_reason, t.version, t.plan_name,
       CASE WHEN t.next_code IS NOT NULL THEN t.next_plan_price ELSE t.price END AS next_price,
       CASE WHEN t.next_code IS NOT NULL THEN t.next_plan_currency ELSE t.currency END
           AS next_currency,
       CASE WHEN t.next_code IS NOT NULL THEN coalesce(t.next_name, t.next_code)
            ELSE t.plan_name END AS next_plan_name,
       public.subscription_next_renewal(
           CASE WHEN t.next_code IS NOT NULL AND t.next_plan_interval <> t.plan_interval
                THEN t.renews_at
                ELSE coalesce(t.renewal_anchor, t.renews_at) END,
           t.renews_at,
           CASE WHEN t.next_code IS NOT NULL THEN t.next_plan_interval ELSE t.plan_interval END,
           tenant.timezone) AS next_period_end
FROM terms t CROSS JOIN tenant;
