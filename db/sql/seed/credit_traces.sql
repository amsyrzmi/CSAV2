-- Which of the credit demo's trace ids a company's ledger already holds (a trace is written whole,
-- in one transaction, so a trace that is there is complete). Run inside the company's scope.
SELECT DISTINCT trace_id
FROM public.ai_usage_events
WHERE client_id = %(client_id)s AND trace_id = ANY(%(trace_ids)s);
