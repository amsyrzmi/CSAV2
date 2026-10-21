SELECT id, request_key, fingerprint, body, action, page_key, resource_id, result, created_at
FROM public.console_workspace_turns
WHERE client_id = %s AND thread_id = %s AND owner_user_id = %s AND request_key = %s;
