SELECT id, trace_id, conversation_id, node, agent, status, duration_ms, summary, created_at
FROM public.agent_events
WHERE client_id = %s
  AND id > %s
  AND (%s::uuid IS NULL OR trace_id = %s::uuid)
ORDER BY id
LIMIT %s;
