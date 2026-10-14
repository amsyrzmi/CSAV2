# Runbook 01 — Replay a Failed Turn

## Objective
Re-queue an ingress event that encountered a transient error or was marked `failed`/`dead` without creating duplicate customer deliveries.

## Safety Guarantees
- Idempotency key and session-level advisory locks prevent double processing or duplicate customer turns.
- Delivery outbox tables (`outbound_deliveries.message_id`) have unique constraints to prevent duplicate deliveries.

## Diagnostic Steps (SQL)

Check failed or dead events for a tenant:
```sql
SELECT id, client_id, attempts, status, last_error, next_attempt_at, created_at
FROM public.ingress_events
WHERE client_id = '<CLIENT_UUID>'
  AND status IN ('failed', 'dead')
ORDER BY created_at DESC
LIMIT 10;
```

## Remediation (SQL)

Re-queue the event for immediate worker pickup:
```sql
UPDATE public.ingress_events
SET status = 'received',
    attempts = 0,
    next_attempt_at = now(),
    last_error = NULL
WHERE id = '<EVENT_UUID>'
  AND client_id = '<CLIENT_UUID>';
```

## Verification
1. Confirm the row status transitions to `dispatched`, then `completed`:
   ```sql
   SELECT id, status, attempts, dispatched_at, completed_at
   FROM public.ingress_events
   WHERE id = '<EVENT_UUID>';
   ```
2. Verify customer message delivery in `outbound_deliveries`:
   ```sql
   SELECT id, status, delivered_at
   FROM public.outbound_deliveries
   WHERE trace_id = (SELECT trace_id FROM public.ingress_events WHERE id = '<EVENT_UUID>');
   ```
