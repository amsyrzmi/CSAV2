-- Remove an email target with the notices and one-time buttons that point at it (their foreign
-- keys have no cascade). Telegram targets are never removed here: the WHERE keeps them. The
-- notice and button deletes run only when the target is an email target of this company.
-- Named parameters: client_id, target_id.
WITH doomed AS (
    SELECT id FROM public.staff_notification_targets
     WHERE client_id = %(client_id)s AND id = %(target_id)s AND channel = 'email'
), actions AS (
    DELETE FROM public.staff_actions
     WHERE client_id = %(client_id)s AND target_id IN (SELECT id FROM doomed)
), notices AS (
    DELETE FROM public.staff_notifications
     WHERE client_id = %(client_id)s AND target_id IN (SELECT id FROM doomed)
)
DELETE FROM public.staff_notification_targets
 WHERE client_id = %(client_id)s AND id IN (SELECT id FROM doomed)
RETURNING id;
