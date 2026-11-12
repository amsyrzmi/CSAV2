-- Records the decision of a request the caller holds locked (and has seen pending).
UPDATE public.signup_requests
SET status = %(status)s, decided_via = %(decided_via)s, decided_by = %(decided_by)s,
    decline_reason = %(decline_reason)s, subscription_id = %(subscription_id)s, decided_at = now()
WHERE client_id = %(client_id)s AND id = %(request_id)s AND status = 'pending'
RETURNING id;
