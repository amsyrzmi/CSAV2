-- Whether the item already holds a summary (read in the job's fenced transaction, whatever the
-- item's status shows), so a summary stage that re-enters after storing it makes no model call.
SELECT summary IS NOT NULL AS stored
FROM public.training_items
WHERE client_id = %(client_id)s AND id = %(item_id)s;
