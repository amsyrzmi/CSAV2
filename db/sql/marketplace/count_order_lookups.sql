-- Order-number lookups this conversation made in the last hour (spec 6.7 abuse limit). The
-- spoke runner records every run in agent_runs (durable, shared by every worker, 30-day
-- retention). A lookup is a run whose answer was "found" or "not found"; a refused run is not.
SELECT count(*) AS used
FROM public.agent_runs
WHERE client_id = %(client_id)s
  AND conversation_id = %(conversation_id)s
  AND agent = 'ecommerce_agent'
  AND created_at >= now() - interval '1 hour'
  AND output -> 'fact_ids' ?| %(fact_ids)s::text[];
