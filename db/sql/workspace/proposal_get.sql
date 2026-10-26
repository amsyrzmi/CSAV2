SELECT id, client_id, user_id, login_session_id, thread_id, kind, payload, payload_sha256, expected_version, card, status, created_at, expires_at, executed_at, result,
       expires_at <= now() AS is_expired
FROM public.console_proposals
WHERE id = %s AND client_id = %s AND user_id = %s
FOR UPDATE;
