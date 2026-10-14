WITH expired AS (
  UPDATE public.approvals
  SET status = 'expired',
      decided_at = now(),
      decided_by = 'system_runbook'
  WHERE client_id = %s
    AND status = 'pending'
    AND expires_at < now()
  RETURNING id, client_id, trace_id, draft
),
aud AS (
  INSERT INTO public.approval_audit (client_id, approval_id, actor, action, old_values, new_values)
  SELECT e.client_id, e.id, 'system_runbook', 'expired',
         jsonb_build_object('status', 'pending', 'draft', e.draft),
         jsonb_build_object('status', 'expired')
  FROM expired e
)
SELECT count(*) AS count FROM expired;
