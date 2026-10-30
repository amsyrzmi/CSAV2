SELECT thread_id,subject,sender,snippet,received_at,labels,unread,category,priority,summary,action_items,analysed_at,latest_body IS NOT NULL AS has_body
FROM public.email_threads WHERE client_id=%s AND mailbox_id=%s ORDER BY received_at DESC NULLS LAST,thread_id LIMIT 200;
