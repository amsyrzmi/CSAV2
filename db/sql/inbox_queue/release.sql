-- The queue side of a hand back or a resolve: the item leaves the queue, nothing waits on a
-- person. The status is 'bot' (hand back) or 'resolved' (resolve, which also records resolved_at).
-- No inbox_version bump here: inbox/set_owner.sql, run in the same transaction, bumps it once.
UPDATE public.conversations c
SET queue_status = %(status)s,
    queue_reason = NULL,
    needs_staff_since = NULL,
    due_at = NULL,
    assignee_user_id = NULL,
    claimed_at = NULL,
    snoozed_until = NULL,
    resolved_at = CASE WHEN %(status)s = 'resolved' THEN now() ELSE c.resolved_at END
WHERE c.client_id = %(client_id)s AND c.id = %(conversation_id)s
RETURNING c.id;
