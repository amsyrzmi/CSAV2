SELECT id, contact_id, status, turn_count
FROM public.conversations
WHERE client_id = %s
  AND channel = %s
  AND channel_thread = %s
