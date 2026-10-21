SELECT id, request_key, body, result, created_at
FROM public.console_workspace_turns
WHERE client_id = %s AND thread_id = %s AND owner_user_id = %s
  AND (%s::bigint IS NULL OR id < %s)
ORDER BY id DESC
LIMIT %s;
