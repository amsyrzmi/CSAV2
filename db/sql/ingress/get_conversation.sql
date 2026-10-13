SELECT id, contact_id, status, turn_count
FROM public.conversations
WHERE client_id = %s
  AND channel = 'telegram'
  AND channel_thread = %s
