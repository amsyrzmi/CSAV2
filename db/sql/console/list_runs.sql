SELECT id, trace_id, conversation_id, agent, ok, status, error_code,
       error_category, latency_ms, created_at
FROM public.agent_runs
WHERE client_id = %s
  AND (%s::bigint IS NULL OR id < %s)
  AND (%s::text IS NULL OR agent = %s)
ORDER BY id DESC
LIMIT %s;
