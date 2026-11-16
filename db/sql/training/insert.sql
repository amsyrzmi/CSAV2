-- Adds a PDF (a video is added by its own upload start). A PDF has no job, so no job columns; the
-- thumbnail join keeps the shape of list.sql (a PDF never has one).
WITH item AS (
    INSERT INTO public.training_items
        (id, client_id, kind, title, view_mode, storage_name, original_name, media_type,
         byte_size, sha256, page_count, created_by, request_key, fingerprint)
    VALUES
        (%(id)s, %(client_id)s, 'pdf', %(title)s, %(view_mode)s, %(storage_name)s,
         %(original_name)s, %(media_type)s, %(byte_size)s, %(sha256)s, %(page_count)s,
         %(created_by)s, %(request_key)s, %(fingerprint)s)
    RETURNING *
)
SELECT i.id, i.kind, i.title, i.description, i.category, i.view_mode, i.page_count, i.byte_size,
       i.created_at, i.created_by, i.status, i.duration_ms, i.failure_code, i.video_codec,
       i.summary IS NOT NULL AS has_summary, t.sha256 AS thumbnail_version
FROM item i
LEFT JOIN public.media_assets t
       ON t.client_id = i.client_id AND t.kind = 'training_thumbnail' AND t.subject_key = i.id::text;
