-- Staged subscription requests; %(status)s is 'open' (no decision row) or 'decided'.
SELECT r.id, r.contact_id, c.full_name, c.phone, c.email, r.action,
       CASE WHEN r.payload->>'subscription_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            THEN (r.payload->>'subscription_id')::uuid END AS subscription_id,
       r.payload->>'plan_code' AS plan_code, r.payload->>'reason' AS reason,
       r.created_at AS requested_at,
       d.decision, d.decided_at, d.decided_by, d.note, d.proposal_id
FROM public.agent_preparation_requests r
JOIN public.contacts c ON c.client_id = r.client_id AND c.id = r.contact_id
LEFT JOIN public.subscription_request_decisions d ON d.client_id = r.client_id AND d.request_id = r.id
WHERE r.client_id = %(client_id)s
  AND r.agent = 'subscription_agent'
  AND r.action IN ('request_pause', 'request_cancel', 'request_change')
  AND (CASE WHEN %(status)s::text = 'open' THEN d.request_id IS NULL ELSE d.request_id IS NOT NULL END)
  AND (%(before)s::uuid IS NULL OR (r.created_at, r.id) <
       (SELECT b.created_at, b.id FROM public.agent_preparation_requests b
         WHERE b.client_id = %(client_id)s AND b.id = %(before)s))
ORDER BY r.created_at DESC, r.id DESC
LIMIT %(limit)s;
