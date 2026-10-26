-- Take (or retake a stale) exclusive execution lease on a proposal.
UPDATE public.console_proposals
SET status = 'confirmed', claim_token = %s, claimed_at = now()
WHERE id = %s AND client_id = %s
RETURNING id, client_id, user_id, login_session_id, thread_id, kind, payload, payload_sha256, expected_version, card, status, created_at, expires_at, executed_at, result;
