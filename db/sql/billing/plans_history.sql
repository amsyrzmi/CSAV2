-- A company's plan history, oldest first (csa.billing.plans). Run inside tx() for the company:
-- row security on app.client_id also limits it to that company. id breaks created_at ties.
SELECT stage, bundle, effective_from, created_at
FROM client_billing_plans
WHERE client_id = %s
ORDER BY effective_from, created_at, id;
