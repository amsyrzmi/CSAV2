-- Start a failure episode for the store: records the safe code and sets problem_notified_at
-- only when no episode is open, so one notification goes out per store per episode. A row
-- comes back only for the call that opened the episode.
INSERT INTO public.marketplace_store_state AS s (
    store_id, client_id, last_error_code, problem_notified_at, updated_at
) VALUES (%(store_id)s, %(client_id)s, %(code)s, %(now)s, %(now)s)
ON CONFLICT (store_id) DO UPDATE SET
    last_error_code = EXCLUDED.last_error_code,
    problem_notified_at = EXCLUDED.problem_notified_at,
    updated_at = EXCLUDED.updated_at
WHERE s.client_id = EXCLUDED.client_id AND s.problem_notified_at IS NULL
RETURNING problem_notified_at;
