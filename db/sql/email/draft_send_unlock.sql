-- Owner recovery after an unknown outcome: only a claim with no Gmail id, older than any Gmail call.
UPDATE public.email_drafts SET status='draft',updated_at=clock_timestamp()
WHERE client_id=%s AND mailbox_id=%s AND id=%s AND status='sending' AND gmail_message_id IS NULL
 AND updated_at<=clock_timestamp()-make_interval(secs=>%s)
RETURNING id,thread_id,recipient,COALESCE(to_addresses,ARRAY[recipient]) AS to_addresses,cc,bcc,subject,body,status,source,gmail_draft_id,sent_at,gmail_message_id,reply_to_message_id,needs_staff_input,created_at,updated_at;
