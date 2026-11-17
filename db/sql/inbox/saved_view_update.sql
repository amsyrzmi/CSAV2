-- Rename a saved view, replace its filters, or both (PUT /inbox/views/{id}); NULL keeps the value.
-- Named parameters: client_id, user_id, view_id, name (text or NULL), filters (jsonb or NULL).
-- No row comes back for a view that is not the asking person's in this company.
UPDATE public.inbox_saved_views
SET name = coalesce(%(name)s::text, name),
    filters = coalesce(%(filters)s::jsonb, filters)
WHERE client_id = %(client_id)s AND user_id = %(user_id)s AND id = %(view_id)s
RETURNING id, name, filters, created_at;
