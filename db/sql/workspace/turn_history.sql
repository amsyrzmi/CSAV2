SELECT body, action, result
FROM public.console_workspace_turns
WHERE client_id = %s AND thread_id = %s AND owner_user_id = %s
ORDER BY id DESC
LIMIT %s;
