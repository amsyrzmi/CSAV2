-- The company's payment settings, or no row while it has never saved them.
SELECT enabled, lead_days, reminder_days, suspend_after_days, version
FROM public.payment_settings
WHERE client_id = %(client_id)s;
