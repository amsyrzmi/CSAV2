SELECT thread_id,subject,sender,snippet,received_at,labels,unread,category,priority,summary,action_items,analysed_at,latest_body,latest_message_id
FROM public.email_threads WHERE client_id=%s AND mailbox_id=%s AND thread_id=%s FOR UPDATE;
