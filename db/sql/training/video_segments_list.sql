-- A video's transcript segments in time order (chunk, then position within the chunk).
SELECT chunk_index, position, start_ms, end_ms, text
FROM public.training_transcript_segments
WHERE client_id = %(client_id)s AND item_id = %(item_id)s
ORDER BY chunk_index, position;
