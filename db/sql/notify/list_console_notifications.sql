-- One person's console notifications in the company, newest first (GET /notifications).
-- Named parameters: client_id, user_id, unread_only (bool), limit. Row security keeps the company;
-- user_id keeps the person (never taken from the request).
SELECT n.id, n.kind, n.priority, n.message_key, n.params, n.link, n.read_at, n.created_at
FROM public.console_notifications n
WHERE n.client_id = %(client_id)s AND n.user_id = %(user_id)s
  AND (NOT %(unread_only)s OR n.read_at IS NULL)
ORDER BY n.created_at DESC, n.id DESC
LIMIT %(limit)s;
