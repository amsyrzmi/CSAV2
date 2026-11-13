-- A video's transcript lines in time order (chunk, then position within the chunk). Read in a plain
-- tenant transaction with no company lock; a video still transcribing has the lines made so far.
SELECT start_ms, end_ms, text
FROM public.training_transcript_segments
WHERE client_id = %(client_id)s AND item_id = %(item_id)s
ORDER BY chunk_index, position;
