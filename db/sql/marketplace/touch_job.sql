-- Heartbeat: the claim is still this worker's (same attempt), so it is not stale. Locks the row.
UPDATE public.marketplace_sync_jobs
SET claimed_at = %(now)s
WHERE client_id = %(client_id)s AND id = %(job_id)s AND status = 'claimed'
  AND attempts = %(attempts)s
RETURNING id;
