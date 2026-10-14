UPDATE public.dlq
SET replay_attempts = replay_attempts + 1,
    replayed = true
WHERE id = %s
  AND client_id = %s
  AND replay_attempts < max_replays
  AND NOT abandoned
RETURNING id, trace_id, stage, envelope;
