-- The open (pending) request of one contact for one plan, if any.
SELECT id
FROM public.signup_requests
WHERE client_id = %(client_id)s AND contact_id = %(contact_id)s AND plan_code = %(plan_code)s
  AND status = 'pending';
