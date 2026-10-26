UPDATE public.console_proposals
SET status = %s,
    result = %s,
    executed_at = CASE WHEN %s::text = 'executed' THEN now() ELSE executed_at END,
    claim_token = NULL,
    claimed_at = NULL
WHERE id = %s AND client_id = %s
RETURNING id, client_id, user_id, login_session_id, thread_id, kind, payload, payload_sha256, expected_version, card, origin, status, created_at, expires_at, executed_at, result;
