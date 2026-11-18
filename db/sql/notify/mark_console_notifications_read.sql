-- Mark the person's unread console notifications read (POST /notifications/read): the ones in
-- ids, or all of them when mark_all. user_id is in the WHERE, so an id that is not the person's
-- matches nothing, whatever the row security says. Returns how many rows changed.
-- Named parameters: client_id, user_id, mark_all (bool), ids (uuid[]).
WITH marked AS (
    UPDATE public.console_notifications n
    SET read_at = now()
    WHERE n.client_id = %(client_id)s AND n.user_id = %(user_id)s AND n.read_at IS NULL
      AND (%(mark_all)s OR n.id = ANY(%(ids)s::uuid[]))
    RETURNING n.id
)
SELECT count(*)::int AS updated FROM marked;
