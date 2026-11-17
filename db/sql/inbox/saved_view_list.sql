-- The asking person's saved views in the company, newest first (GET /inbox/views).
-- Named parameters: client_id, user_id. Row security keeps the company; user_id keeps the person.
SELECT v.id, v.name, v.filters, v.created_at
FROM public.inbox_saved_views v
WHERE v.client_id = %(client_id)s AND v.user_id = %(user_id)s
ORDER BY v.created_at DESC, v.id DESC;
