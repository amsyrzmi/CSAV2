-- Claim under the per-mailbox advisory lock, before Gmail is called; only a plain draft is claimed.
UPDATE public.email_drafts SET status='sending',updated_at=clock_timestamp()
WHERE client_id=%s AND mailbox_id=%s AND id=%s AND status='draft' RETURNING id;
