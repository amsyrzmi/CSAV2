SELECT id,email,display_name,is_platform_admin,is_enabled,must_change_password FROM console_users ORDER BY created_at DESC LIMIT %s;
