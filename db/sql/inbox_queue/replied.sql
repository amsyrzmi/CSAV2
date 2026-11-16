-- A person's reply, or an approved or edited held reply: waiting on the customer. Claims for the
-- author when the item is unclaimed and the author is a console user. No inbox_version bump: the
-- caller's own writes already cover it.
UPDATE public.conversations c
SET queue_status = 'waiting_customer',
    queue_reason = NULL,
    needs_staff_since = NULL,
    due_at = NULL,
    snoozed_until = NULL,
    resolved_at = NULL,
    last_staff_reply_at = now(),
    assignee_user_id = coalesce(c.assignee_user_id, %(user_id)s::uuid),
    claimed_at = CASE WHEN c.assignee_user_id IS NULL AND %(user_id)s::uuid IS NOT NULL
                      THEN now() ELSE c.claimed_at END
WHERE c.client_id = %(client_id)s AND c.id = %(conversation_id)s
RETURNING c.assignee_user_id;
