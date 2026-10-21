SELECT id, sequence, created_at
FROM public.console_workspace_threads
WHERE client_id = %s AND owner_user_id = %s
  AND (%s::bigint IS NULL OR sequence < %s)
ORDER BY sequence DESC
LIMIT %s;
