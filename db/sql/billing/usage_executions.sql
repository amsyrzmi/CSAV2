-- Every ledger row of the executions (distinct trace_id) whose FIRST row falls in [start, end)
-- (csa.billing.service). A trace's first row is in the window when it has a row in the window and
-- none before its start; all of its rows come back, also those after the end (a job straddling
-- the boundary belongs to the cycle of its first call). Run inside tx() for the company: row
-- security on app.client_id also limits it to that company. The window scan uses
-- (client_id, occurred_at); the per-trace lookups use (client_id, trace_id).
-- Rows come grouped by trace, each trace's rows in call order (id breaks occurred_at ties), so the
-- first row of each group is the execution's first call.
WITH traces AS (
    SELECT DISTINCT w.trace_id
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
      )
)
SELECT e.trace_id, e.occurred_at, e.job, e.provider, e.model, e.prompt_tokens, e.completion_tokens
FROM traces t
JOIN ai_usage_events e ON e.client_id = %(client_id)s AND e.trace_id = t.trace_id
ORDER BY e.trace_id, e.occurred_at, e.id;
