-- What needs attention, from stored analyses only: not noise, not already answered by the owner;
-- priority first, then the latest message newest first.
SELECT thread_id,sender,subject,priority,summary,action_items FROM public.email_threads
WHERE client_id=%s AND mailbox_id=%s AND analysed_at IS NOT NULL AND latest_from_owner IS NOT TRUE
 AND category<>ALL(%s)
ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
 received_at DESC NULLS LAST,thread_id LIMIT %s;
