SELECT id, email, display_name, is_platform_admin, must_change_password
FROM public.console_users WHERE email = %s;
