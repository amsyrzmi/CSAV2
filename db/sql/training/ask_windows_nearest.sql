-- The %(limit)s transcript windows of one video nearest the question's embedding, for
-- training_ask on a transcript too long to send whole, returned in time order. Only windows made
-- by the same embedding model as the question (%(embed_model)s) are compared; none back means
-- the video cannot be searched with this model. The vector arrives as pgvector text ('[0.1,...]').
SELECT start_ms, end_ms, text
FROM (
    SELECT position, start_ms, end_ms, text
    FROM public.training_transcript_windows
    WHERE client_id = %(client_id)s
      AND item_id = %(item_id)s
      AND embed_model = %(embed_model)s
    ORDER BY embedding <=> %(vector)s::public.vector, position
    LIMIT %(limit)s
) AS nearest
ORDER BY start_ms, position;
