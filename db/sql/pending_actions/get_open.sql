SELECT id, client_id, conversation_id, contact_id, agent, action, payload, status, expires_at
FROM pending_actions
WHERE id = %s
  AND client_id = %s
  AND contact_id = %s
  AND status = 'open'
  AND expires_at > now();
