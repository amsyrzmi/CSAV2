-- Only after a clear refusal: nothing was sent, so the draft can be edited and sent again.
UPDATE public.email_drafts SET status='draft',send_claim_id=NULL,updated_at=clock_timestamp()
WHERE client_id=%s AND mailbox_id=%s AND id=%s AND status='sending' AND send_claim_id=%s RETURNING id;
