-- One chunk's kept segments, in order, with times in milliseconds from the start of the video.
-- Run in the chunk's fenced transaction after video_chunk_done.sql, so they are written once.
INSERT INTO public.training_transcript_segments
    (client_id, item_id, chunk_index, position, start_ms, end_ms, text)
SELECT %(client_id)s, %(item_id)s, %(chunk_index)s, s.position, s.start_ms, s.end_ms, s.text
FROM unnest(%(positions)s::int[], %(starts)s::int[], %(ends)s::int[], %(texts)s::text[])
    AS s(position, start_ms, end_ms, text);
