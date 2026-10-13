SELECT id, client_id, trace_id, conversation_id, message_id, attempts,
       last_status_code, last_error
FROM public.outbound_deliveries
WHERE status = 'delivering'
  AND locked_at < now() - make_interval(secs => %s)
ORDER BY locked_at, id
LIMIT %s
FOR UPDATE SKIP LOCKED;
