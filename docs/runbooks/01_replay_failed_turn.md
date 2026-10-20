# Runbook 01 — Replay a Failed Turn

> **Caution:** This is an operator reference, not approval to modify a live
> database. Review [runbook status and authorization](./README.md) first.

## Objective
Re-queue an ingress event that encountered a transient error or was marked `failed`/`dead` without creating duplicate customer deliveries.

## Safety considerations
The worker uses ordering/idempotency controls, but re-running a turn may repeat
business actions or produce another delivery where the operation lacks a durable
receipt. Review the event, its downstream effects and the current retry contract
before authorizing a replay; do not assume the outbox prevents every duplicate.

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

The tested programmatic primitive is `csa.ops.runbooks.requeue_turn`, subject to
the caller's authorized tenant transaction. The SQL below is a historical sketch,
not a production command. Inspect the current parameterized query and tests first.
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
1. Inspect the resulting status and attempts. It may complete or fail again; do not
   assume requeue means successful completion:
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
