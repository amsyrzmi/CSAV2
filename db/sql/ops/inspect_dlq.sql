SELECT id, client_id, trace_id, stage, envelope, error, replay_attempts, max_replays, replayed, abandoned, created_at
FROM public.dlq
WHERE client_id = %s
ORDER BY created_at DESC
LIMIT %s;
