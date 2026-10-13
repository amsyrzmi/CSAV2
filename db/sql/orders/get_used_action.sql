SELECT id, agent, action, payload
FROM public.pending_actions
WHERE id = %s
  AND client_id = %s
  AND contact_id = %s
  AND status = 'used';
