UPDATE public.console_sessions SET revoked_at = now()
WHERE user_id = ANY(%s) AND revoked_at IS NULL;
