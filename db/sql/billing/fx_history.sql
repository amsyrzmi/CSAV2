-- Every USD to MYR row, oldest first: the shadow rule's existing dates and the audit's "before"
-- (platform admin, csa.console.billing_admin). Run under the config_lock.sql lock for FX.
SELECT usd_to_myr, effective_from
FROM billing_fx_rates
ORDER BY effective_from;
