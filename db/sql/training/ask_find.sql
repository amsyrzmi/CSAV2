-- The company's videos the staff assistant may answer a question about (training_ask), newest
-- first as the library lists them: the one with %(item_id)s, or those whose title holds every one
-- of %(words)s (case-insensitive; an empty array matches every title), in one of %(states)s
-- (normally only 'ready'). At most 6, so the caller knows when there are more than 5 to offer.
-- Read in a plain tenant transaction; row security keeps it to the caller's company.
SELECT id, title, status, speech, duration_ms
FROM public.training_items
WHERE client_id = %(client_id)s
  AND kind = 'video'
  AND status = ANY(%(states)s::text[])
  AND (%(item_id)s::uuid IS NULL OR id = %(item_id)s::uuid)
  AND NOT EXISTS (
      SELECT 1
      FROM unnest(%(words)s::text[]) AS w(word)
      WHERE strpos(lower(title), lower(w.word)) = 0
  )
ORDER BY created_at DESC, id DESC
LIMIT 6;
