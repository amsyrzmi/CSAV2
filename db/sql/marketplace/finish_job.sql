-- End this worker's claim (same attempt): done, failed, cancelled, or back to pending with a
-- retry time. Going back to pending folds into a newer pending duplicate (cancelled) if one
-- exists. No row back means the claim was lost (requeued as stale meanwhile). refund_attempt
-- hands a claim back without spending its attempt (a backfill that reached its page cap).
UPDATE public.marketplace_sync_jobs AS j
SET status = CASE
        WHEN %(status)s::text = 'pending' AND EXISTS (
            SELECT 1 FROM public.marketplace_sync_jobs d
            WHERE d.client_id = j.client_id AND d.store_id = j.store_id AND d.kind = j.kind
              AND coalesce(d.target, '') = coalesce(j.target, '') AND d.status = 'pending'
        ) THEN 'cancelled'
        ELSE %(status)s::text
    END,
    attempts = j.attempts - CASE WHEN %(refund_attempt)s::boolean THEN 1 ELSE 0 END,
    last_error_code = %(error_code)s::text,
    next_attempt_at = coalesce(%(retry_at)s::timestamptz, j.next_attempt_at),
    claimed_at = NULL
WHERE j.client_id = %(client_id)s AND j.id = %(job_id)s AND j.status = 'claimed'
  AND j.attempts = %(attempts)s
RETURNING j.status;
