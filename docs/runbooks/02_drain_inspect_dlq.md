# Runbook 02 — Drain and Inspect DLQ

## Objective
Inspect events that exceeded max attempts and were pushed to `dlq`, diagnose the root cause, and re-drive eligible items.

## Diagnostic Steps (SQL)

List open DLQ entries:
```sql
SELECT id, client_id, trace_id, stage, replay_attempts, max_replays, replayed, abandoned, error, created_at
FROM public.dlq
WHERE client_id = '<CLIENT_UUID>'
ORDER BY created_at DESC
LIMIT 20;
```

Inspect error details for a specific item:
```sql
SELECT stage, error, envelope
FROM public.dlq
WHERE id = <DLQ_ID>;
```

## Remediation (SQL)

### 1. Re-drive an eligible turn
If the root cause (e.g. temporary downstream network partition) is resolved:
```sql
-- Step A: Mark DLQ row replayed
UPDATE public.dlq
SET replay_attempts = replay_attempts + 1,
    replayed = true
WHERE id = <DLQ_ID>
  AND client_id = '<CLIENT_UUID>'
  AND replay_attempts < max_replays
  AND NOT abandoned;

-- Step B: Re-insert into ingress_events with clean idempotency key
INSERT INTO public.ingress_events (
  client_id, endpoint_id, contact_id, idempotency_key, payload_hash, trace_id, raw
)
SELECT
  d.client_id,
  (d.envelope->'meta'->>'endpoint_id')::uuid,
  (d.envelope->'contact'->>'id')::uuid,
  'redrive:' || d.id || ':' || d.trace_id,
  'redrive_hash',
  d.trace_id,
  jsonb_build_object('envelope', d.envelope, 'source', jsonb_build_object('channel', 'redrive_dlq'))
FROM public.dlq d
WHERE d.id = <DLQ_ID>;
```

### 2. Abandon unfixable items
If an item contains invalid customer input that cannot be recovered:
```sql
UPDATE public.dlq
SET abandoned = true
WHERE id = <DLQ_ID>;
```
