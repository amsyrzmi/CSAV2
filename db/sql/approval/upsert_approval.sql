INSERT INTO public.approvals
    (client_id, conversation_id, trace_id, draft, reason, risk_score, ui, expires_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (trace_id) DO UPDATE SET trace_id = EXCLUDED.trace_id
RETURNING id, status, expires_at;
