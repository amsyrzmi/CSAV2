UPDATE public.email_drafts SET status='sent',sent_at=clock_timestamp(),gmail_message_id=%s,updated_at=clock_timestamp()
WHERE client_id=%s AND mailbox_id=%s AND id=%s AND status='sending' AND send_claim_id=%s RETURNING sent_at;
