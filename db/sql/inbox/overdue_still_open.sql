-- Is a held inbox.overdue alert still true when it is about to go out? (csa.inbox_queue.overdue_check)
-- The conversation, in this company, is still in Needs us (queue_status 'needs_staff') and the wait
-- the alert was written for is still the current one: needs_staff_since equals the alert's cycle.
-- A resolve, a snooze, a hand-back or a new customer message after a reply moves one or the other.
-- Named parameters: client_id, conversation_id, cycle (timestamptz).
SELECT EXISTS (
    SELECT 1 FROM public.conversations c
    WHERE c.client_id = %(client_id)s
      AND c.id = %(conversation_id)s
      AND c.queue_status = 'needs_staff'
      AND c.needs_staff_since = %(cycle)s
) AS still_open;
