-- A video's search windows with their embeddings and the model that made them. Run in the job's
-- fenced transaction right after video_transcript_clear.sql (segments false), so the windows are
-- replaced as a whole. Embeddings arrive as pgvector text ('[0.1,...]').
INSERT INTO public.training_transcript_windows
    (client_id, item_id, position, start_ms, end_ms, text, embedding, embed_model)
SELECT %(client_id)s, %(item_id)s, w.position, w.start_ms, w.end_ms, w.text,
       w.embedding::public.vector, %(embed_model)s::text
FROM unnest(%(positions)s::int[], %(starts)s::int[], %(ends)s::int[], %(texts)s::text[],
            %(embeddings)s::text[])
    AS w(position, start_ms, end_ms, text, embedding);
