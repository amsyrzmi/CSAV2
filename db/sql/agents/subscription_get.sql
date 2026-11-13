-- One subscription of the chat's own contact: an id of anyone else's matches nothing. The opening
-- invoice columns are as in subscription_list.sql.
SELECT s.id, s.plan, p.name AS plan_name, p.billing_interval, s.status, s.price, s.currency,
       s.renews_at, s.cancel_at_period_end, s.resume_at, s.pending_plan, pp.name AS pending_plan_name,
       s.pending_effective_at, oi.status AS opening_invoice_status,
       oi.reference AS opening_invoice_reference
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
WHERE s.client_id = %(client_id)s AND s.contact_id = %(contact_id)s AND s.id = %(subscription_id)s;
