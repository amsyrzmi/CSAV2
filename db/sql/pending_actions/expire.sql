UPDATE pending_actions
SET status = 'expired'
WHERE id = %s
  AND client_id = %s
  AND contact_id = %s
  AND status = 'open'
  AND expires_at <= now();
