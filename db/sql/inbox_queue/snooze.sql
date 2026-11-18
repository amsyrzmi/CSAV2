-- Hidden until the given time; the claim stays (the holder is told when it returns), the clock stops.
UPDATE public.conversations
SET queue_status = 'snoozed',
    snoozed_until = %s,
    queue_reason = NULL,
    needs_staff_since = NULL,
    due_at = NULL,
    resolved_at = NULL,
    inbox_version = inbox_version + 1
WHERE client_id = %s AND id = %s
RETURNING id, snoozed_until;
