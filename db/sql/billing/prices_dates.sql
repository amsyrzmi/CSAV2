-- The effective dates already held for one model, for the shadow-rule check.
SELECT effective_from FROM ai_model_prices
WHERE provider = %(provider)s AND model = %(model)s
ORDER BY effective_from;
