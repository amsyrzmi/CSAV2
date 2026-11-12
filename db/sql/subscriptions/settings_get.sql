-- The company's subscription settings, or no row while it has never saved them.
SELECT pending_expiry_days, version
FROM public.subscription_settings
WHERE client_id = %(client_id)s;
