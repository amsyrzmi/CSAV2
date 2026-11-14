-- Every USD to MYR row, newest first. The pure lookup picks the row in force.
SELECT usd_to_myr, effective_from
FROM billing_fx_rates
ORDER BY effective_from DESC;
