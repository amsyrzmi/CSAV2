-- One finished map step of the summary, saved in the job's parts, in the job's fenced transaction.
-- A part saved earlier under the same index is replaced, so saving twice keeps one. Fenced on this
-- attempt (stage, status and attempts), and it refreshes the heartbeat. No row back: the job is not
-- this attempt's, so the caller writes nothing more.
UPDATE public.training_video_jobs
SET parts = coalesce(
        (SELECT jsonb_agg(p)
         FROM jsonb_array_elements(parts) AS p
         WHERE p->>'index' IS DISTINCT FROM %(index)s::int::text),
        '[]'::jsonb
    ) || jsonb_build_array(%(part)s::jsonb),
    locked_at = now()
WHERE client_id = %(client_id)s
  AND item_id = %(item_id)s
  AND stage = %(stage)s::text
  AND status = 'processing'
  AND attempts = %(attempt)s
RETURNING jsonb_array_length(parts) AS parts;
