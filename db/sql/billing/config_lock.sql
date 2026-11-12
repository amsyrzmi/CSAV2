-- One price or FX write at a time per key ('ai_model_prices:<provider>:<model>' or
-- 'billing_fx_rates'), until the transaction ends, so two admins cannot both pass the shadow rule.
SELECT pg_advisory_xact_lock(hashtextextended(%s, 0));
