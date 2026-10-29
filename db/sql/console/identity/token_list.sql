SELECT t.id,t.user_id,t.client_id,u.email,u.display_name,t.role,t.kind,
       t.expires_at,t.consumed_at,t.revoked_at,t.created_at,t.hr_preset
FROM console_tokens t JOIN console_users u ON u.id=t.user_id
WHERE t.client_id IS NOT DISTINCT FROM %s ORDER BY t.created_at DESC LIMIT %s;
