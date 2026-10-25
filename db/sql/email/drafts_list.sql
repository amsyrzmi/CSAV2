SELECT id,thread_id,recipient,subject,body,status,source,gmail_draft_id,created_at,updated_at FROM public.email_drafts
WHERE client_id=%s AND mailbox_id=%s ORDER BY updated_at DESC LIMIT 100;
