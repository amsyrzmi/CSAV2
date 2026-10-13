INSERT INTO public.agent_events
    (client_id, trace_id, conversation_id, node, agent, status, duration_ms, summary)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
