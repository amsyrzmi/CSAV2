-- Sends in the rolling last 60 minutes: sent rows by sent_at, claimed or unknown rows by their claim time.
SELECT count(*)::int AS sends,
 COALESCE(ceil(extract(epoch FROM min(COALESCE(sent_at,updated_at))+interval '60 minutes'-clock_timestamp())),0)::int AS retry_after
FROM public.email_drafts
WHERE client_id=%s AND mailbox_id=%s
 AND ((status='sent' AND sent_at>clock_timestamp()-interval '60 minutes')
  OR (status='sending' AND updated_at>clock_timestamp()-interval '60 minutes'));
