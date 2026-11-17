-- Delete one of the asking person's saved views (DELETE /inbox/views/{id}).
-- Named parameters: client_id, user_id, view_id. No row comes back for anyone else's view.
DELETE FROM public.inbox_saved_views
WHERE client_id = %(client_id)s AND user_id = %(user_id)s AND id = %(view_id)s
RETURNING id;
