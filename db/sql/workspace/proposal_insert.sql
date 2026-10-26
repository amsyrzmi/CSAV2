INSERT INTO public.console_proposals
    (id, client_id, user_id, login_session_id, thread_id, kind, payload, payload_sha256,
     expected_version, card, status, created_at, expires_at)
VALUES (coalesce(%s::uuid, gen_random_uuid()), %s, %s, %s, %s, %s, %s, %s, %s, %s, 'requested', now(), now() + interval '15 minutes')
ON CONFLICT (id) DO NOTHING
RETURNING id, client_id, user_id, login_session_id, thread_id, kind, payload, payload_sha256, expected_version, card, status, created_at, expires_at, executed_at, result;
