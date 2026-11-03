-- The knowledge base becomes its own add-on: each company with Customer support gets a
-- knowledge_base row with the same status and dates, so nobody loses anything.
-- Preconditions: 0450 applied. Capability switches are not touched, so bots keep answering.
-- Rollback: DELETE FROM public.console_service_packages WHERE reference = 'backfill-0530';
-- BACKFILL
INSERT INTO public.console_service_packages
    (client_id, package, status, requested_by, requested_at, activated_by, activated_at, reference)
SELECT client_id, 'knowledge_base', status, requested_by, requested_at, activated_by, activated_at,
       'backfill-0530'
FROM public.console_service_packages
WHERE package = 'customer_support'
ON CONFLICT (client_id, package) DO NOTHING;
