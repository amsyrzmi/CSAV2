-- Claim the tenant's next due job, skipping stores that already have a claimed job (per-store
-- concurrency of one). Runs after lock_claims.sql in the same transaction.
UPDATE public.marketplace_sync_jobs AS j
SET status = 'claimed', attempts = j.attempts + 1, claimed_at = %(now)s
WHERE j.id = (
    SELECT p.id
    FROM public.marketplace_sync_jobs p
    WHERE p.client_id = %(client_id)s
      AND p.status = 'pending'
      AND p.next_attempt_at <= %(now)s
      AND NOT EXISTS (
          SELECT 1 FROM public.marketplace_sync_jobs c
          WHERE c.client_id = p.client_id AND c.store_id = p.store_id AND c.status = 'claimed'
      )
    ORDER BY p.next_attempt_at, p.created_at, p.id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
  AND j.status = 'pending'
RETURNING j.id, j.client_id, j.store_id, j.kind, j.target, j.attempts, j.claimed_at, j.created_at;
