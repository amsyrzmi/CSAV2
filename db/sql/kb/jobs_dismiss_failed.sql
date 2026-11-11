-- Take every failed, partial and cancelled job of the tenant off the list. Param: client_id.
UPDATE public.kb_ingest_jobs
   SET dismissed_at = now()
 WHERE client_id = %s
   AND dismissed_at IS NULL
   AND status IN ('failed', 'partial', 'cancelled')
RETURNING id;
