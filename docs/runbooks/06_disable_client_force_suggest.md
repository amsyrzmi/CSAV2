# Runbook 06 — Disable Client or Force Suggest Mode

> **Caution:** This is an operator reference, not approval to modify a live tenant.
> Review [runbook status and authorization](./README.md) first.

## Objective
Change tenant response policy to `suggest` or suspend a tenant, subject to the
in-flight and queued-work limitations below.

## Scenarios and limits
1. **Urgent incident / quarantine:** Client requests immediate freeze of automated messaging.
2. **Review mode:** Require human staff approval for all drafts prior to sending (`suggest` mode).

Changing policy does not cancel already accepted or in-flight work and is not an
instantaneous kill switch. Confirm the current ingress/worker behavior and handle
already queued actions explicitly under an approved incident procedure.

## Remediation (SQL)

### Option A: Force Suggest Mode
The tested programmatic primitive is `csa.ops.runbooks.set_deployment_mode` in an
authorized tenant transaction. This changes policy for work that reads the updated
configuration; it does not recall already generated/sent work. The SQL below is
illustrative only.
```sql
UPDATE public.clients
SET deployment_mode = 'suggest',
    config = jsonb_set(
        COALESCE(config, '{}'::jsonb),
        '{policy,deployment_mode}',
        '"suggest"'
    ),
    updated_at = now()
WHERE id = '<CLIENT_UUID>';
```
This mode requests approval for replies governed by the normal reply gate. It does
not override paths that bypass that gate or work already in flight.

### Option B: Suspend Tenant
```sql
UPDATE public.clients
SET status = 'suspended',
    updated_at = now()
WHERE id = '<CLIENT_UUID>';
```
Do not assume this SQL statement is an immediate quarantine or stops work already
accepted by a worker. Verify the actual deployed ingress/worker enforcement and
queued-work handling before using any suspension procedure.

### Option C: Restore Normal Automated Operations
```sql
UPDATE public.clients
SET status = 'active',
    deployment_mode = 'auto',
    config = jsonb_set(
        COALESCE(config, '{}'::jsonb),
        '{policy,deployment_mode}',
        '"auto"'
    ),
    updated_at = now()
WHERE id = '<CLIENT_UUID>';
```
