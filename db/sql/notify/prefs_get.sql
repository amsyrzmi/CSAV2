-- The person's saved notification preferences in this company, or no row (email off, defaults).
-- Named parameters: client_id, user_id.
SELECT email_enabled, kinds, min_priority
FROM public.console_notification_prefs
WHERE client_id = %(client_id)s AND user_id = %(user_id)s;
