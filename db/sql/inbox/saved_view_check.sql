-- What a saved view write checks, under the person's advisory lock (inbox_saved_views.py).
-- Named parameters: client_id, user_id; name (the new name, NULL when it does not change);
--   view_id (the view being changed, NULL on a create).
--   n           the person's views in the company (the API allows 20)
--   name_taken  another of the person's views has this name ignoring case, the rule of the unique
--               index inbox_saved_views_name_key (client_id, user_id, lower(name))
--   found       view_id is one of the person's views in the company
SELECT count(*)::int AS n,
       coalesce(bool_or(lower(v.name) = lower(%(name)s::text)
                        AND v.id IS DISTINCT FROM %(view_id)s::uuid), false) AS name_taken,
       coalesce(bool_or(v.id = %(view_id)s::uuid), false) AS found
FROM public.inbox_saved_views v
WHERE v.client_id = %(client_id)s AND v.user_id = %(user_id)s;
