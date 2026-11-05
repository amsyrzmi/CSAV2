-- A store that is pending, disconnected or expired: its pending jobs are cancelled. A claimed
-- job is left to its worker, which re-checks the store before every write.
UPDATE public.marketplace_sync_jobs
SET status = 'cancelled', last_error_code = %(code)s
WHERE client_id = %(client_id)s AND store_id = %(store_id)s AND status = 'pending'
RETURNING id;
