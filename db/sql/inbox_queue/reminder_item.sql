-- The re-check read of an Inbox reminder handler (csa.inbox_queue.reminders): the item's queue
-- state when the reminder fires, without a lock (a handler that changes the item, the snooze
-- wake, takes the row lock through csa.inbox_queue.transitions).
-- in_queue repeats the scope of get_item.sql: telegram and whatsapp only (not the sandbox), never
-- a console chat thread or participant.
-- human_muted is the /release route's rule (plan 2a decision 11): a person owns the chat and the
-- assistant's mute has not run out.
-- customer is csa.console.inbox_contact.display_name_or_none in SQL: the stored name trimmed,
-- NULL when it is blank or only the contact's own phone number (WhatsApp stores "+<number>" as
-- the name of a customer with no profile name). Never message text.
-- staff_acted_since_claim: a person did something to the item after it was claimed (any queue
-- event with an actor later than claimed_at; the claim's own event carries the same time).
-- woke_this_cycle: the current needs-us clock was started by a snooze ending (a 'woke' event at
-- needs_staff_since; the wake writes both in one transaction, so with the same now()).
-- Named parameters: client_id, conversation_id.
SELECT c.queue_status, c.priority, c.assignee_user_id, c.claimed_at, c.needs_staff_since,
       c.due_at, c.snoozed_until, c.last_staff_reply_at,
       (c.channel IN ('telegram', 'whatsapp')
         AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id)
         AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p
                          WHERE p.contact_id = c.contact_id)) AS in_queue,
       (c.owned_by = 'human_support' AND
        (c.ai_muted_until IS NULL OR c.ai_muted_until > now())) IS TRUE AS human_muted,
       CASE
           WHEN n.name = '' THEN NULL
           WHEN coalesce(k.phone, '') <> ''
                AND n.name ~ '^[+0-9[:space:]().-]+$'
                AND regexp_replace(n.name, '[^0-9]', '', 'g')
                    = regexp_replace(k.phone, '[^0-9]', '', 'g') THEN NULL
           ELSE n.name
       END AS customer,
       EXISTS (SELECT 1 FROM public.inbox_queue_events e
                WHERE e.client_id = c.client_id AND e.conversation_id = c.id
                  AND e.actor_user_id IS NOT NULL AND e.created_at > c.claimed_at)
           AS staff_acted_since_claim,
       EXISTS (SELECT 1 FROM public.inbox_queue_events e
                WHERE e.client_id = c.client_id AND e.conversation_id = c.id
                  AND e.action = 'woke' AND e.created_at = c.needs_staff_since)
           AS woke_this_cycle
FROM public.conversations c
LEFT JOIN public.contacts k ON k.client_id = c.client_id AND k.id = c.contact_id
CROSS JOIN LATERAL (
    SELECT regexp_replace(coalesce(k.full_name, ''), '^[[:space:]]+|[[:space:]]+$', '', 'g') AS name
) n
WHERE c.client_id = %(client_id)s AND c.id = %(conversation_id)s;
