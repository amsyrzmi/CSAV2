UPDATE public.email_drafts SET recipient=%s,to_addresses=%s,cc=%s,bcc=%s,subject=%s,body=%s,needs_staff_input=%s,source='human',updated_at=clock_timestamp()
WHERE client_id=%s AND mailbox_id=%s AND id=%s AND status='draft'
RETURNING id,thread_id,recipient,COALESCE(to_addresses,ARRAY[recipient]) AS to_addresses,cc,bcc,subject,body,status,source,gmail_draft_id,sent_at,gmail_message_id,reply_to_message_id,needs_staff_input,created_at,updated_at;
