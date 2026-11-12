-- The number of executions (distinct trace_id) whose FIRST row falls in [start, end), for the
-- unbilled-usage warnings (csa.billing.service: usage with no plan in force, usage after a
-- termination). The same trace rule as usage_executions.sql. Run inside tx() for the company.
SELECT count(DISTINCT w.trace_id) AS n
FROM ai_usage_events w
WHERE w.client_id = %(client_id)s
  AND w.occurred_at >= %(start)s
  AND w.occurred_at < %(end)s
  AND NOT EXISTS (
      SELECT 1
      FROM ai_usage_events p
      WHERE p.client_id = %(client_id)s
        AND p.trace_id = w.trace_id
        AND p.occurred_at < %(start)s
  );
