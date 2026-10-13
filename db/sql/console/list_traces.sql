SELECT trace_id,
       min(conversation_id::text)::uuid AS conversation_id,
       min(created_at) AS started_at,
       max(created_at) AS finished_at,
       count(*) AS steps,
       bool_or(status = 'error') AS had_error,
       max(summary ->> 'route') AS route,
       max(summary ->> 'intent') AS intent,
       max(summary ->> 'decision') AS gate_decision,
       array_remove(array_agg(DISTINCT agent), NULL) AS agents
FROM public.agent_events
WHERE client_id = %s
GROUP BY trace_id
HAVING (%s::timestamptz IS NULL OR min(created_at) < %s::timestamptz)
ORDER BY min(created_at) DESC
LIMIT %s;
