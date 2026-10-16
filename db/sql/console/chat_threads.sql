SELECT id, sequence, archived_at, created_at
FROM public.console_chat_threads
WHERE client_id = %s AND user_id = %s AND archived_at IS NULL
  AND (%s::bigint IS NULL OR sequence < %s)
ORDER BY sequence DESC LIMIT %s;
