-- Every ledger row's tokens for one company with occurred_at in [start, end), billed or not
-- (usage before a plan or after a termination included): what the Langfuse mirror was sent, for
-- the platform-admin reconciliation (csa.billing.langfuse_check). Run inside tx() for the company.
SELECT
    coalesce(sum(prompt_tokens), 0)::bigint AS prompt_tokens,
    coalesce(sum(completion_tokens), 0)::bigint AS completion_tokens
FROM ai_usage_events
WHERE client_id = %(client_id)s
  AND occurred_at >= %(start)s
  AND occurred_at < %(end)s;
