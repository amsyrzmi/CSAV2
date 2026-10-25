INSERT INTO public.email_threads(client_id,mailbox_id,thread_id,subject,sender,snippet,latest_message_id,latest_body,received_at,labels,unread,synced_at)
VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())
ON CONFLICT (client_id,mailbox_id,thread_id) DO UPDATE SET subject=excluded.subject,sender=excluded.sender,snippet=excluded.snippet,
 latest_body=excluded.latest_body,received_at=excluded.received_at,labels=excluded.labels,unread=excluded.unread,synced_at=clock_timestamp(),
 latest_message_id=excluded.latest_message_id,
 category=CASE WHEN public.email_threads.latest_message_id IS DISTINCT FROM excluded.latest_message_id THEN NULL ELSE public.email_threads.category END,
 priority=CASE WHEN public.email_threads.latest_message_id IS DISTINCT FROM excluded.latest_message_id THEN NULL ELSE public.email_threads.priority END,
 summary=CASE WHEN public.email_threads.latest_message_id IS DISTINCT FROM excluded.latest_message_id THEN NULL ELSE public.email_threads.summary END,
 action_items=CASE WHEN public.email_threads.latest_message_id IS DISTINCT FROM excluded.latest_message_id THEN '[]'::jsonb ELSE public.email_threads.action_items END,
 analysed_at=CASE WHEN public.email_threads.latest_message_id IS DISTINCT FROM excluded.latest_message_id THEN NULL ELSE public.email_threads.analysed_at END;
