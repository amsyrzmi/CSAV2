UPDATE public.email_drafts SET status='exported',gmail_draft_id=%s,updated_at=clock_timestamp()
WHERE client_id=%s AND mailbox_id=%s AND id=%s AND status='draft'
RETURNING id,thread_id,recipient,subject,body,status,source,gmail_draft_id,created_at,updated_at;
