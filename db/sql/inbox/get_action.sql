SELECT fingerprint, result FROM public.console_inbox_actions
WHERE client_id = %s AND actor_id = %s AND request_key = %s;
