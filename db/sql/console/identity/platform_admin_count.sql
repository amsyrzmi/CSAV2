SELECT count(*) AS count FROM console_users WHERE is_platform_admin AND is_enabled AND password_hash IS NOT NULL;
