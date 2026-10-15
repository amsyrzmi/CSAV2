SELECT s.id AS session_id,s.user_id,s.client_id,s.csrf_hash,s.expires_at,s.last_seen_at,
u.email,u.display_name,u.is_platform_admin,u.is_enabled,u.must_change_password
FROM console_sessions s JOIN console_users u ON u.id=s.user_id
WHERE s.token_hash=%s AND s.revoked_at IS NULL AND s.expires_at>now()
AND s.last_seen_at>now()-make_interval(secs => %s) FOR UPDATE OF s;
