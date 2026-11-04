-- Unsent drafts (draft, sending, exported) before sent ones, so older unsent drafts never fall
-- behind sent rows past the limit; newest change first within each group.
SELECT id,thread_id,recipient,COALESCE(to_addresses,ARRAY[recipient]) AS to_addresses,cc,bcc,subject,body,status,source,gmail_draft_id,sent_at,gmail_message_id,reply_to_message_id,needs_staff_input,created_at,updated_at FROM public.email_drafts
WHERE client_id=%s AND mailbox_id=%s ORDER BY (status='sent'),updated_at DESC,id LIMIT 100;
