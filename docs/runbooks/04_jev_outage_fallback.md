# Runbook 04 — Jev Outage or Latency Spike Fallback

## Objective
Instantly switch a tenant's decision provider from Jev/cascade to pure LLM (Groq) or shadow mode during an upstream Jev outage or latency spike.

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

Force decision provider to `llm` (pure Groq classification):
```sql
UPDATE public.clients
SET config = jsonb_set(
        COALESCE(config, '{}'::jsonb),
        '{policy,decide_mode}',
        '"llm"'
    )
WHERE id = '<CLIENT_UUID>';
```

Alternatively, set to `shadow` to log Jev asynchronously without blocking turn completion:
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
New turns will immediately bypass live Jev calls and use the LLM provider.
