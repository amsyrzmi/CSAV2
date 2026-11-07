-- Store a summary on an existing row; never inserts, so a removal that committed during the
-- model call leaves no row behind. With replace false only the first writer wins (summary IS
-- NULL); with replace true the stored summary is overwritten. "replaced" says whether one was.
WITH previous AS (
    SELECT id, summary IS NOT NULL AS replaced
    FROM public.training_items
    WHERE client_id = %(client_id)s AND id = %(id)s
    FOR UPDATE
)
UPDATE public.training_items AS t
SET summary = %(summary)s,
    summary_truncated = %(truncated)s,
    summary_model = %(model)s,
    summarised_at = now()
FROM previous
WHERE t.client_id = %(client_id)s
  AND t.id = previous.id
  AND (%(replace)s::boolean OR t.summary IS NULL)
RETURNING t.id, t.storage_name, t.byte_size, t.summary, t.summary_truncated, t.summary_model,
          t.summarised_at, previous.replaced;
