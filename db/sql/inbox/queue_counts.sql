-- The queue tab counts of one company (GET /inbox/queue/counts and the list's `counts`).
-- Named parameters: client_id, user_id (the person asking: "mine" is theirs).
--   needs_us          needs_staff
--   mine              assigned to user_id and needs_staff or waiting_customer
--   unassigned        needs_staff with no assignee
--   overdue           needs_staff and due_at in the past
--   waiting_customer  waiting_customer
--   snoozed           snoozed
--   oldest_waiting_at the earliest needs_staff_since of the needs_staff rows
-- One branch per state, so each reads its own partial index (conversations_queue_due_idx,
-- _waiting_idx, _snoozed_idx) and the bot and resolved rows, the large majority, are never read.
-- The list's exclusions apply to the rows found, written as in the list: OFFSET 0 in each NOT
-- EXISTS is an optimisation fence, not a limit, so they stay per-row index probes on the rows the
-- partial indexes give and are never turned into anti joins over the chat tables.
WITH queued AS (
    SELECT c.id, c.contact_id, c.channel, c.queue_status, c.assignee_user_id, c.due_at,
           c.needs_staff_since
    FROM public.conversations c
    WHERE c.client_id = %(client_id)s AND c.queue_status = 'needs_staff'
    UNION ALL
    SELECT c.id, c.contact_id, c.channel, c.queue_status, c.assignee_user_id, c.due_at,
           c.needs_staff_since
    FROM public.conversations c
    WHERE c.client_id = %(client_id)s AND c.queue_status = 'waiting_customer'
    UNION ALL
    SELECT c.id, c.contact_id, c.channel, c.queue_status, c.assignee_user_id, c.due_at,
           c.needs_staff_since
    FROM public.conversations c
    WHERE c.client_id = %(client_id)s AND c.queue_status = 'snoozed'
)
SELECT count(*) FILTER (WHERE q.queue_status = 'needs_staff')::int AS needs_us,
       count(*) FILTER (WHERE q.assignee_user_id = %(user_id)s::uuid
                          AND q.queue_status IN ('needs_staff', 'waiting_customer'))::int AS mine,
       count(*) FILTER (WHERE q.queue_status = 'needs_staff'
                          AND q.assignee_user_id IS NULL)::int AS unassigned,
       count(*) FILTER (WHERE q.queue_status = 'needs_staff' AND q.due_at < now())::int AS overdue,
       count(*) FILTER (WHERE q.queue_status = 'waiting_customer')::int AS waiting_customer,
       count(*) FILTER (WHERE q.queue_status = 'snoozed')::int AS snoozed,
       min(q.needs_staff_since) FILTER (WHERE q.queue_status = 'needs_staff') AS oldest_waiting_at
FROM queued q
WHERE q.channel IN ('telegram', 'whatsapp')
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = q.id OFFSET 0)
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p
                  WHERE p.contact_id = q.contact_id OFFSET 0);
