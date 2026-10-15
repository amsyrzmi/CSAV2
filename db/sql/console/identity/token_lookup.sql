SELECT t.*,u.email,u.display_name,u.password_hash,u.is_enabled,u.is_platform_admin FROM console_tokens t
JOIN console_users u ON u.id=t.user_id WHERE t.token_hash=%s AND t.consumed_at IS NULL
AND t.revoked_at IS NULL AND t.expires_at>now() FOR UPDATE OF t,u;
