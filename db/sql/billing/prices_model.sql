-- One model's price rows, oldest first: the shadow rule's existing dates and the audit's "before"
-- (platform admin, csa.console.billing_admin). Run under the config_lock.sql lock for the model.
SELECT provider, model, input_usd_per_mtok, output_usd_per_mtok, effective_from
FROM ai_model_prices
WHERE provider = %(provider)s AND model = %(model)s
ORDER BY effective_from;
