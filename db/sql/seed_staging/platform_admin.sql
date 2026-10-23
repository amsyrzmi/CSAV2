SELECT id, email, display_name FROM public.console_users
WHERE is_platform_admin AND is_enabled ORDER BY created_at LIMIT 1;
