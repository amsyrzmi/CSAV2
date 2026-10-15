SELECT count(*) AS count FROM console_memberships m JOIN console_users u ON u.id=m.user_id
WHERE m.client_id=%s AND m.role='tenant_admin' AND m.is_enabled AND u.is_enabled AND u.password_hash IS NOT NULL;
