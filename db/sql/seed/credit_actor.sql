-- The platform admin the credit demo's plan rows are audited under: the earliest enabled one.
-- Plan rows are only ever written by a platform admin (csa.billing.plans.insert_plan).
SELECT id, display_name
FROM public.console_users
WHERE is_platform_admin AND is_enabled
ORDER BY created_at, id
LIMIT 1;
