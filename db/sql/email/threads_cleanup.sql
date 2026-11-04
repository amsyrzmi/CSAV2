-- Caps for one mailbox: keep the newest N threads (by latest-message time, undated last) and drop
-- any whose latest message is older than the cutoff. Their analyses live on the thread row and go
-- with it. Drafts are never deleted: every draft linked to a removed thread keeps existing with
-- its thread and reply links cleared, in the same statement as the delete.
WITH ranked AS (
 SELECT thread_id,received_at,row_number() OVER (ORDER BY received_at DESC NULLS LAST,thread_id) AS rank
 FROM public.email_threads WHERE client_id=%s AND mailbox_id=%s),
doomed AS (
 SELECT thread_id FROM ranked WHERE rank>%s OR received_at<%s),
unlinked AS (
 UPDATE public.email_drafts SET thread_id=NULL,reply_to_message_id=NULL
 WHERE client_id=%s AND mailbox_id=%s AND thread_id IN (SELECT thread_id FROM doomed)
 RETURNING 1)
DELETE FROM public.email_threads WHERE client_id=%s AND mailbox_id=%s
 AND thread_id IN (SELECT thread_id FROM doomed)
RETURNING thread_id;
