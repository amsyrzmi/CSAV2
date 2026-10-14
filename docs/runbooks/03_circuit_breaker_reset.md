# Runbook 03 — Circuit Breaker Reset

## Objective
Diagnose an open circuit breaker on an agent/spoke and manually close it once the downstream service is healthy.

## Diagnostic Steps (SQL)

Check circuit breaker states across all capabilities for a tenant:
```sql
SELECT name, display_name, circuit_state, fail_count, opened_at, updated_at
FROM public.capability_registry
WHERE client_id = '<CLIENT_UUID>'
ORDER BY name;
```

A row with `circuit_state = 'open'` means the agent tripped after repeated consecutive failures and is failing fast to protect latency.

## Remediation (SQL)

Once downstream health is verified (e.g. database query, external API status):
```sql
UPDATE public.capability_registry
SET circuit_state = 'closed',
    fail_count = 0,
    opened_at = NULL,
    updated_at = now()
WHERE client_id = '<CLIENT_UUID>'
  AND name = '<AGENT_NAME>';
```

## Verification
Confirm the circuit is now closed:
```sql
SELECT name, circuit_state, fail_count
FROM public.capability_registry
WHERE client_id = '<CLIENT_UUID>'
  AND name = '<AGENT_NAME>';
```
Future customer turns requesting this agent will now execute normally.
