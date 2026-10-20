# Runbook 04 — Jev Outage or Latency Spike Fallback

> **Caution:** This is an operator reference, not approval to modify a live
> tenant configuration. Review [runbook status and authorization](./README.md) first.

## Objective
Select a tenant decision mode after diagnosing an upstream Jev outage or latency
spike. This configuration change is not an instant kill switch for in-flight turns.

## Diagnostic Steps
Check `decision_log` for elevated latencies, errors, or fallbacks:
```sql
SELECT mode, provider, avg(latency_ms) AS avg_ms, count(*) AS turns,
       count(*) FILTER (WHERE fallback_reason IS NOT NULL) AS fallbacks
FROM public.decision_log
WHERE client_id = '<CLIENT_UUID>'
  AND created_at > now() - interval '1 hour'
GROUP BY mode, provider;
```

## Remediation (SQL)

The tested programmatic primitive is `csa.ops.runbooks.force_decide_mode` within
the authorized tenant transaction. The SQL below is illustrative only.

Force decision provider to `llm` (LLM-only classification):
```sql
UPDATE public.clients
SET config = jsonb_set(
        COALESCE(config, '{}'::jsonb),
        '{policy,decide_mode}',
        '"llm"'
    )
WHERE id = '<CLIENT_UUID>';
```

Alternatively, set to `shadow` to use the LLM result while comparing/logging Jev.
Both classifiers run concurrently, but the turn awaits both; this mode is not
non-blocking and is not an outage fallback. Prefer `llm` when Jev itself is the
unavailable dependency.
```sql
UPDATE public.clients
SET config = jsonb_set(
        COALESCE(config, '{}'::jsonb),
        '{policy,decide_mode}',
        '"shadow"'
    )
WHERE id = '<CLIENT_UUID>';
```

## Verification
Confirm the client configuration is updated:
```sql
SELECT id, config->'policy'->>'decide_mode' AS decide_mode
FROM public.clients
WHERE id = '<CLIENT_UUID>';
```
Subsequent turns that load the updated tenant configuration use the selected mode.
