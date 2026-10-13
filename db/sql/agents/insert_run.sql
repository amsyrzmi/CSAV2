INSERT INTO public.agent_runs
    (client_id, trace_id, conversation_id, agent, task_id, ok, status, error_code,
     error_category, latency_ms, model, prompt_tokens, completion_tokens, input, output)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id;
