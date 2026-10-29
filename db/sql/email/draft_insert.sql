INSERT INTO public.email_drafts(client_id,mailbox_id,id,thread_id,recipient,to_addresses,cc,bcc,subject,body,status,source,reply_to_message_id,needs_staff_input)
VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft',%s,%s,%s)
RETURNING id,thread_id,recipient,COALESCE(to_addresses,ARRAY[recipient]) AS to_addresses,cc,bcc,subject,body,status,source,gmail_draft_id,sent_at,gmail_message_id,reply_to_message_id,needs_staff_input,created_at,updated_at;
