-- A new USD to MYR row (platform admin, Task 10). Rows are never edited or removed; the shadow
-- rule (csa.billing.config.check_new_price_date) is checked by the caller before this runs.
INSERT INTO billing_fx_rates (usd_to_myr, effective_from)
VALUES (%(usd_to_myr)s, %(effective_from)s)
RETURNING id, created_at;
