UPDATE console_sessions SET revoked_at=now() WHERE id=%s AND revoked_at IS NULL;
