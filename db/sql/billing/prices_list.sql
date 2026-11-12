-- Every model price row, newest first within a model. The pure lookups pick the row in force.
SELECT provider, model, input_usd_per_mtok, output_usd_per_mtok, effective_from
FROM ai_model_prices
ORDER BY provider, model, effective_from DESC;
