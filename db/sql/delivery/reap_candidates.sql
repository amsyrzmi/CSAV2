-- has_attachment marks an image message (the only outbound row with an attachment): a stale image
-- row gets two attempts, a text row five.
SELECT d.id, d.client_id, d.trace_id, d.conversation_id, d.message_id, d.attempts,
       d.last_status_code, d.last_error,
       coalesce((SELECT m.has_attachment FROM public.messages m
                 WHERE m.id = d.message_id AND m.client_id = d.client_id), false)
           AS has_attachment
FROM public.outbound_deliveries d
WHERE d.status = 'delivering'
  AND d.locked_at < now() - make_interval(secs => %s)
ORDER BY d.locked_at, d.id
LIMIT %s
FOR UPDATE SKIP LOCKED;
