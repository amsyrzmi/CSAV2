-- Claims abandoned by a crashed worker go back to pending, or fail once attempts are used up.
-- If a newer pending job for the same work exists, the stale one is cancelled (folded into it)
-- instead, since the pending-unique index allows only one.
UPDATE public.marketplace_sync_jobs AS j
SET status = CASE
        WHEN j.attempts >= %(max_attempts)s THEN 'failed'
        WHEN EXISTS (
            SELECT 1 FROM public.marketplace_sync_jobs d
            WHERE d.client_id = j.client_id AND d.store_id = j.store_id AND d.kind = j.kind
              AND coalesce(d.target, '') = coalesce(j.target, '') AND d.status = 'pending'
        ) THEN 'cancelled'
        ELSE 'pending'
    END,
    last_error_code = 'claim_abandoned',
    claimed_at = NULL,
    next_attempt_at = %(now)s
WHERE j.client_id = %(client_id)s
  AND j.status = 'claimed'
  AND j.claimed_at < %(now)s - make_interval(secs => %(stale_after)s)
RETURNING j.id, j.store_id, j.kind, j.status;
