UPDATE public.console_users SET must_change_password = true, updated_at = now()
WHERE id = ANY(%s) AND NOT is_platform_admin;
