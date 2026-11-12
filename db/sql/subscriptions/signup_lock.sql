-- Locks one sign-up request for a decision; the caller re-checks the status under the lock, so a
-- staff approval, a decline and an auto-approval cannot both win.
SELECT id, contact_id, plan_code, full_name, status
FROM public.signup_requests
WHERE client_id = %(client_id)s AND id = %(request_id)s
FOR UPDATE;
