INSERT INTO public.decision_log
    (client_id, trace_id, node, mode, provider, model_version, latency_ms, answers, final,
     disagree, fallback_reason)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
