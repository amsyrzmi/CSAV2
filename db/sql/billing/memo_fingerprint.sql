-- A cheap fingerprint of everything a closed cycle's figures read besides the ledger
-- (csa.billing.memo): the four global config tables and the company's plan rows. Every one is
-- append-only, so a new row raises its count and its max id; max(created_at) also tells apart a
-- table truncated and refilled to the same count (test and staging resets). Run inside tx() for
-- the company: row security on app.client_id also limits the plan rows to it.
SELECT
    (SELECT count(*) FROM ai_model_prices) AS prices_n,
    (SELECT max(id) FROM ai_model_prices) AS prices_id,
    (SELECT max(created_at) FROM ai_model_prices) AS prices_at,
    (SELECT count(*) FROM billing_fx_rates) AS fx_n,
    (SELECT max(id) FROM billing_fx_rates) AS fx_id,
    (SELECT max(created_at) FROM billing_fx_rates) AS fx_at,
    (SELECT count(*) FROM billing_stage_fees) AS fees_n,
    (SELECT max(id) FROM billing_stage_fees) AS fees_id,
    (SELECT max(created_at) FROM billing_stage_fees) AS fees_at,
    (SELECT count(*) FROM billing_markup_tiers) AS tiers_n,
    (SELECT max(id) FROM billing_markup_tiers) AS tiers_id,
    (SELECT max(created_at) FROM billing_markup_tiers) AS tiers_at,
    (SELECT count(*) FROM client_billing_plans WHERE client_id = %(client_id)s) AS plans_n,
    (SELECT max(id) FROM client_billing_plans WHERE client_id = %(client_id)s) AS plans_id,
    (SELECT max(created_at) FROM client_billing_plans WHERE client_id = %(client_id)s) AS plans_at;
