-- A completed upload: the item becomes uploaded with the size that arrived and the SHA-256 of its
-- piece hashes, and its upload and piece rows go (so it no longer counts as uploading, and a later
-- removal does not try to abort a finished upload). Run under the company lock with the upload row
-- locked; no row back means the item was no longer uploading.
WITH done AS (
    UPDATE public.training_items
    SET status = 'uploaded', byte_size = %(byte_size)s, sha256 = %(sha256)s, updated_at = now()
    WHERE client_id = %(client_id)s AND id = %(item_id)s AND status = 'uploading'
    RETURNING *
), pieces AS (
    DELETE FROM public.training_video_pieces p
    USING done
    WHERE p.client_id = done.client_id AND p.item_id = done.id
), upload AS (
    DELETE FROM public.training_video_uploads u
    USING done
    WHERE u.client_id = done.client_id AND u.item_id = done.id
)
SELECT d.id, d.kind, d.title, d.description, d.category, d.view_mode, d.page_count, d.byte_size,
       d.created_at, d.created_by, d.status, d.duration_ms, d.failure_code, d.video_codec,
       false AS has_summary, t.sha256 AS thumbnail_version
FROM done d
LEFT JOIN public.media_assets t
       ON t.client_id = d.client_id AND t.kind = 'training_thumbnail' AND t.subject_key = d.id::text;
