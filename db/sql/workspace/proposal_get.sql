SELECT id, client_id, user_id, login_session_id, thread_id, kind, payload, payload_sha256, expected_version, card, origin, status, created_at, expires_at, executed_at, result,
       claim_token, claimed_at,
       expires_at <= now() AS is_expired,
       -- Claim lease (keep in step with csa.workspace.registry.CLAIM_LEASE).
       (claimed_at > now() - interval '2 minutes') IS TRUE AS claim_live
FROM public.console_proposals
WHERE id = %s AND client_id = %s AND user_id = %s
FOR UPDATE;
