-- A needs_staff item's reason changes; its clock and due time stay. No inbox_version bump.
UPDATE public.conversations
SET queue_reason = %s
WHERE client_id = %s AND id = %s AND queue_status = 'needs_staff'
RETURNING id;
