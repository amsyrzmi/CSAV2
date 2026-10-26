UPDATE public.console_proposals
SET status = %s,
    result = %s,
    executed_at = CASE WHEN %s::text = 'executed' THEN now() ELSE executed_at END
WHERE id = %s AND client_id = %s
RETURNING id, client_id, user_id, login_session_id, thread_id, kind, payload, payload_sha256, expected_version, status, created_at, expires_at, executed_at, result;
