-- One training item for the staff assistant: the open record's summary line (title, length,
-- whether a transcript exists) and, for a long video's answer, its stored summary. The summary
-- and has_transcript are only set for a ready video that has speech. Plain tenant transaction.
SELECT i.id, i.kind, i.title, i.status, i.speech, i.duration_ms,
       coalesce(i.kind = 'video' AND i.status = 'ready' AND i.speech = 'speech'
                AND EXISTS (SELECT 1
                              FROM public.training_transcript_segments s
                             WHERE s.client_id = i.client_id AND s.item_id = i.id), false)
           AS has_transcript,
       CASE WHEN i.kind = 'video' AND i.status = 'ready' AND i.speech = 'speech'
            THEN i.summary END AS summary
FROM public.training_items i
WHERE i.client_id = %(client_id)s AND i.id = %(item_id)s;
