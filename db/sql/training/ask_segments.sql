-- A video's whole transcript for training_ask, in time order (chunk, then position in the chunk).
-- Plain tenant transaction; row security keeps it to the caller's company.
SELECT start_ms, end_ms, text
FROM public.training_transcript_segments
WHERE client_id = %(client_id)s AND item_id = %(item_id)s
ORDER BY chunk_index, position;
