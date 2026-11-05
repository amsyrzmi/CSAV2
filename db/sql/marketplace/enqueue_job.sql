-- Queue one sync job for a TikTok store of the caller's tenant. Duplicate pending triggers fold
-- into the existing job through marketplace_sync_jobs_pending_key (job_id comes back NULL).
-- store_found is false when the store is not this tenant's TikTok store (RLS hides others).
WITH store AS (
    SELECT c.id
    FROM public.channel_credentials c
    WHERE c.client_id = %(client_id)s AND c.id = %(store_id)s AND c.channel = 'tiktok_shop'
), queued AS (
    INSERT INTO public.marketplace_sync_jobs (client_id, store_id, kind, target)
    SELECT %(client_id)s, store.id, %(kind)s::text, %(target)s::text
    FROM store
    ON CONFLICT (client_id, store_id, kind, (coalesce(target, ''))) WHERE status = 'pending'
    DO NOTHING
    RETURNING id
)
SELECT EXISTS (SELECT 1 FROM store) AS store_found, (SELECT id FROM queued) AS job_id;
