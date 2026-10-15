UPDATE console_sessions SET revoked_at=now() WHERE user_id=%s AND client_id=%s AND revoked_at IS NULL;
