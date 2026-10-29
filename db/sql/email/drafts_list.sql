SELECT id,thread_id,recipient,COALESCE(to_addresses,ARRAY[recipient]) AS to_addresses,cc,bcc,subject,body,status,source,gmail_draft_id,sent_at,gmail_message_id,reply_to_message_id,needs_staff_input,created_at,updated_at FROM public.email_drafts
WHERE client_id=%s AND mailbox_id=%s ORDER BY updated_at DESC LIMIT 100;
