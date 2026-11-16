-- A person sets the priority; a needs_staff item's due time follows from its unchanged clock.
UPDATE public.conversations
SET priority = %(priority)s,
    due_at = CASE WHEN queue_status = 'needs_staff'
                  THEN needs_staff_since + make_interval(mins => %(target_min)s)
                  ELSE due_at END,
    inbox_version = inbox_version + 1
WHERE client_id = %(client_id)s AND id = %(conversation_id)s
RETURNING id;
