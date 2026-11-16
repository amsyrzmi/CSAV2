-- The queue state of one conversation, read after csa.staff.ownership.lock_conversation.
-- in_queue repeats the scope of public.inbox_queue_enter (0622) and inbox/list_conversations.sql:
-- telegram and whatsapp only (not the sandbox), never a console chat thread or participant.
SELECT c.queue_status, c.queue_reason, c.priority, c.topic, c.assignee_user_id, c.claimed_at,
       c.needs_staff_since, c.due_at, c.snoozed_until, c.last_customer_at, c.last_staff_reply_at,
       (c.channel IN ('telegram', 'whatsapp')
         AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id)
         AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p
                          WHERE p.contact_id = c.contact_id)) AS in_queue,
       EXISTS (SELECT 1 FROM public.approvals a
                WHERE a.client_id = c.client_id AND a.conversation_id = c.id
                  AND a.status = 'pending' AND a.expires_at > now()) AS held_reply_pending
FROM public.conversations c
WHERE c.client_id = %s AND c.id = %s;
