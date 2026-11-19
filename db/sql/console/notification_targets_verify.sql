-- A test mail was delivered to the target: it is verified from now on. Email targets only.
-- Named parameters: client_id, target_id.
UPDATE public.staff_notification_targets
SET verified_at = now()
WHERE client_id = %(client_id)s AND id = %(target_id)s AND channel = 'email'
RETURNING id;
