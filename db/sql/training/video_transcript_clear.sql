-- Remove a video's search windows, and its segments too when %(segments)s is true (a video found
-- to hold no speech keeps neither). Run in the job's fenced transaction.
WITH windows AS (
    DELETE FROM public.training_transcript_windows
    WHERE client_id = %(client_id)s AND item_id = %(item_id)s
)
DELETE FROM public.training_transcript_segments
WHERE client_id = %(client_id)s AND item_id = %(item_id)s AND %(segments)s::boolean;
