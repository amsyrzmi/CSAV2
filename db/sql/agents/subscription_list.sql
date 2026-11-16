-- The chat's own contact's subscriptions, newest first. Both ids come from the turn envelope,
-- never from the planner. With live_only, only the statuses a customer can still use: 'pending'
-- is named here before the status exists (the check refuses it until a later migration adds it).
-- opening_invoice_status and opening_invoice_reference: a pending subscription's opening invoice
-- (the one with no period yet) while it is open; NULL otherwise.
-- plan_has_picture: the plan is still active and the company has a picture for it
-- (media_assets, kind 'plan', keyed by the plan code); an EXISTS, so no row is multiplied,
-- and an archived plan's picture is never asked for. `mine` and `status` send it only when
-- they name exactly one subscription (csa/agents/staged/service.py).
SELECT s.id, s.plan, p.name AS plan_name, p.billing_interval, s.status, s.price, s.currency,
       s.started_at, s.renews_at, s.cancel_at_period_end, s.resume_at, s.pending_plan, pp.name AS pending_plan_name,
       s.pending_effective_at, oi.status AS opening_invoice_status,
       oi.reference AS opening_invoice_reference,
       (p.is_active IS TRUE AND EXISTS (
           SELECT 1 FROM public.media_assets a
           WHERE a.client_id = %(client_id)s AND a.kind = 'plan' AND a.subject_key = p.code
       )) AS plan_has_picture
FROM public.subscriptions s
LEFT JOIN public.plans p ON p.client_id = s.client_id AND p.code = s.plan
LEFT JOIN public.plans pp ON pp.client_id = s.client_id AND pp.code = s.pending_plan
LEFT JOIN LATERAL (
    SELECT v.status, coalesce(v.reference, v.id) AS reference
    FROM public.invoices v
    WHERE s.status = 'pending' AND v.client_id = s.client_id AND v.subscription_id = s.id
      AND v.period_start IS NULL
      AND v.status IN ('issued', 'overdue', 'proof_submitted', 'cash_pending')
    ORDER BY v.issued_at, v.id
    LIMIT 1
) oi ON true
WHERE s.client_id = %(client_id)s AND s.contact_id = %(contact_id)s
  AND (NOT %(live_only)s OR s.status IN ('active', 'paused', 'pending'))
ORDER BY s.started_at DESC, s.id DESC
LIMIT 50;
