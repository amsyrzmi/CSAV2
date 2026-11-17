-- Conversation metadata for the action routes and their receipts: ids, states and times only (no
-- contact field, message text or name). Positional parameters: client_id, conversation_id.
SELECT c.id, c.channel, c.status, c.owned_by, c.ai_muted_until, c.inbox_version,
       c.opened_at,
       (c.owned_by = 'human_support' AND
        (c.ai_muted_until IS NULL OR c.ai_muted_until > now())) IS TRUE AS human_muted,
       c.queue_status, c.queue_reason, c.topic, c.priority, c.assignee_user_id AS assignee_id,
       c.claimed_at, c.needs_staff_since, c.due_at, c.snoozed_until, c.last_customer_at,
       c.last_staff_reply_at
FROM public.conversations c
WHERE c.client_id = %s AND c.id = %s AND c.channel IN ('telegram','whatsapp')
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id)
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p WHERE p.contact_id = c.contact_id);
