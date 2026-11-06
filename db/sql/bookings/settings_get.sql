-- The company's booking settings, or no row while it has never saved them.
SELECT reminder_minutes, pending_hours, customer_limit, version
FROM public.booking_settings
WHERE client_id = %(client_id)s;
