SELECT client_id, role FROM console_memberships WHERE user_id = %s AND is_enabled ORDER BY created_at, client_id;
