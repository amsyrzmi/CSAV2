SELECT id, client_id, conversation_id, trace_id, draft, reason, status, ui, expires_at
FROM public.approvals
WHERE client_id = %s
  AND id = %s
FOR UPDATE;
