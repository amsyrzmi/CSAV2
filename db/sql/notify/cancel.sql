-- A claimed row whose alert is no longer true (the held inbox.overdue re-check): finished without
-- sending, no failure counted. Only while this worker still holds the claim.
UPDATE public.staff_notifications
SET status = 'cancelled', locked_at = NULL
WHERE id = %s AND client_id = %s AND status = 'delivering'
RETURNING id;
