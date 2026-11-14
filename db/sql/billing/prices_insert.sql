-- A new model price row (platform admin, Task 10). Rows are never edited or removed; the shadow
-- rule (csa.billing.config.check_new_price_date) is checked by the caller before this runs.
INSERT INTO ai_model_prices (provider, model, input_usd_per_mtok, output_usd_per_mtok, effective_from)
VALUES (%(provider)s, %(model)s, %(input_usd_per_mtok)s, %(output_usd_per_mtok)s, %(effective_from)s)
RETURNING id, provider, model, input_usd_per_mtok, output_usd_per_mtok, effective_from, created_at;
