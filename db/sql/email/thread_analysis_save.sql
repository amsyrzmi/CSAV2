-- Saves only while the thread's latest message is still the one analysed: a sync that stored a
-- newer message during the model call wins, and that thread is analysed again later.
UPDATE public.email_threads SET category=%s,priority=%s,summary=%s,action_items=%s,analysed_at=clock_timestamp()
WHERE client_id=%s AND mailbox_id=%s AND thread_id=%s AND latest_message_id IS NOT DISTINCT FROM %s;
