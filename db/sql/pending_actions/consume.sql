UPDATE pending_actions
SET status = 'used'
WHERE id = %s
  AND client_id = %s
  AND contact_id = %s
  AND status = 'open'
  AND expires_at > now()
RETURNING id, client_id, conversation_id, contact_id, agent, action, payload, status, expires_at;
