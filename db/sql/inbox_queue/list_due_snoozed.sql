-- Snoozed items whose time has come, in ascending id order (the order they are locked in).
-- Served by conversations_queue_snoozed_idx (client_id, snoozed_until) WHERE queue_status = 'snoozed'.
SELECT c.id
FROM public.conversations c
WHERE c.client_id = %s AND c.queue_status = 'snoozed' AND c.snoozed_until <= now()
ORDER BY c.id
LIMIT %s;
