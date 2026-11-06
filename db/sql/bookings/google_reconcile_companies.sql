-- Which live companies have someone connected, for the reconcile job. Run as the owner, for this
-- list only: everything else for a company runs in its own transaction, as csa_app.
SELECT DISTINCT c.client_id
FROM public.booking_calendar_connections c
JOIN public.clients cl ON cl.id = c.client_id
WHERE c.status = 'connected' AND cl.status IN ('pilot', 'active')
ORDER BY c.client_id;
