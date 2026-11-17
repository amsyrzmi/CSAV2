-- A new saved view for the asking person (POST /inbox/views), after saved_view_check.sql.
-- Named parameters: client_id, user_id, name, filters (jsonb, an InboxFilters dump).
INSERT INTO public.inbox_saved_views (client_id, user_id, name, filters)
VALUES (%(client_id)s, %(user_id)s, %(name)s, %(filters)s)
RETURNING id, name, filters, created_at;
