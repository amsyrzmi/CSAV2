UPDATE console_sessions SET revoked_at=now() WHERE token_hash=%s AND revoked_at IS NULL;
