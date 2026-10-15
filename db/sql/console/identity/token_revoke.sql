UPDATE console_tokens SET revoked_at=now() WHERE id=%s AND consumed_at IS NULL AND revoked_at IS NULL;
