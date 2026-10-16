SELECT id, trace_id, conversation_id, agent, ok, status, error_code,
       error_category, latency_ms, created_at
FROM public.agent_runs
WHERE client_id = %s AND id = %s;
