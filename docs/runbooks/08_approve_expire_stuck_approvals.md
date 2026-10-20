# Runbook 08 — Approve or Expire Stuck Approvals

> **Caution:** This is an operator reference, not approval to modify a live
> tenant/database. Review [runbook status and authorization](./README.md) first.

## Objective
Identify pending staff approval drafts that exceeded their expiration deadline (`expires_at < now()`) and clean them up with full audit logging.

## Diagnostic Steps (SQL)

Find stuck or expired approvals:
```sql
SELECT a.id, a.client_id, a.trace_id, a.status, a.reason, a.expires_at, a.created_at
FROM public.approvals a
WHERE a.client_id = '<CLIENT_UUID>'
  AND a.status = 'pending'
  AND a.expires_at < now()
ORDER BY a.expires_at ASC;
```

## Remediation (SQL)

Expire all overdue approvals and create corresponding `approval_audit` records:
```sql
WITH expired AS (
  UPDATE public.approvals
  SET status = 'expired',
      decided_at = now(),
      decided_by = 'system_runbook'
  WHERE client_id = '<CLIENT_UUID>'
    AND status = 'pending'
    AND expires_at < now()
  RETURNING id, client_id, trace_id, draft
),
aud AS (
  INSERT INTO public.approval_audit (
    client_id, approval_id, actor, action, old_values, new_values
  )
  SELECT
    e.client_id,
    e.id,
    'system_runbook',
    'expired',
    jsonb_build_object('status', 'pending', 'draft', e.draft),
    jsonb_build_object('status', 'expired')
  FROM expired e
)
SELECT count(*) AS expired_count FROM expired;
```

## Automated expiry status
The scheduled `pending_actions_expiry` job expires pending actions; it does not
expire staff `approvals`. The tested `csa.ops.runbooks.expire_stuck_approvals`
primitive writes the approval and audit transition in one tenant-scoped operation,
but this is not an operator-facing CLI. The SQL above is illustrative and must not
be run without a reviewed, authorized procedure.
