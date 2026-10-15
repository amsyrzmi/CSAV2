UPDATE console_sessions SET csrf_hash=%s WHERE id=%s AND revoked_at IS NULL;
