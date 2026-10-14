# Runbook 06 — Disable Client or Force Suggest Mode

## Objective
Immediately suspend automated customer responses for a tenant or enforce human approval (`suggest` mode) for every outbound reply.

## Scenarios
1. **Urgent incident / quarantine:** Client requests immediate freeze of automated messaging.
2. **Review mode:** Require human staff approval for all drafts prior to sending (`suggest` mode).

## Remediation (SQL)

### Option A: Force Suggest Mode (All Drafts Require Staff Approval)
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
*Result:* All customer responses will produce an alert with an Approve/Reject/Edit button in the staff chat. No message is sent until approved.

### Option B: Suspend Tenant (Immediate Quarantine)
```sql
UPDATE public.clients
SET status = 'suspended',
    updated_at = now()
WHERE id = '<CLIENT_UUID>';
```
*Result:* Gateway reject / worker bypass stops processing turns for this tenant.

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
