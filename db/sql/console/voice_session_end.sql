UPDATE public.console_voice_sessions SET status='ended'
WHERE client_id=%s AND id=%s AND status='active' RETURNING id;
