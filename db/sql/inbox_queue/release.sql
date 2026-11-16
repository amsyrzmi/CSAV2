-- The queue side of a hand back or a resolve: the item leaves the queue, nothing waits on a
-- person. The status is 'bot' (hand back) or 'resolved' (resolve, which also records resolved_at).
-- bump: true when the AI already had the chat, so inbox/set_owner.sql did not run (and did not
-- bump inbox_version); false after set_owner.sql, which bumped it once already.
UPDATE public.conversations c
SET queue_status = %(status)s,
    queue_reason = NULL,
    needs_staff_since = NULL,
    due_at = NULL,
    assignee_user_id = NULL,
    claimed_at = NULL,
    snoozed_until = NULL,
    resolved_at = CASE WHEN %(status)s = 'resolved' THEN now() ELSE c.resolved_at END,
    inbox_version = c.inbox_version + CASE WHEN %(bump)s THEN 1 ELSE 0 END
WHERE c.client_id = %(client_id)s AND c.id = %(conversation_id)s
RETURNING c.id;
