INSERT INTO pending_actions (
    client_id, conversation_id, contact_id, agent, action, payload, expires_at
)
SELECT
    %s, conversation.id, contact.id, %s, %s, %s, now() + %s
FROM conversations AS conversation
JOIN contacts AS contact
  ON contact.client_id = conversation.client_id
WHERE conversation.id = %s
  AND conversation.client_id = %s
  AND contact.id = %s
RETURNING id, client_id, conversation_id, contact_id, agent, action, payload, status, expires_at;
