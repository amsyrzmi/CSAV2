SELECT * FROM public.console_voice_sessions
WHERE client_id=%s AND user_id=%s AND login_session_id=%s AND request_key=%s FOR UPDATE;
