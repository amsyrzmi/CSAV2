-- When the company's earliest ledger row happened, or NULL with no usage (csa.billing.service,
-- the cycle picker). Run inside tx() for the company; uses (client_id, occurred_at).
SELECT min(occurred_at) AS first_at
FROM ai_usage_events
WHERE client_id = %s;
