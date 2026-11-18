-- The person's unread console notifications in the company (GET /notifications and /count).
-- Named parameters: client_id, user_id. Served by console_notifications_unread_idx.
SELECT count(*)::int AS unread
FROM public.console_notifications n
WHERE n.client_id = %(client_id)s AND n.user_id = %(user_id)s AND n.read_at IS NULL;
