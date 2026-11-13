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
SELECT id, kind, title, description, category, view_mode, page_count, byte_size, created_at,
       created_by, status, duration_ms, failure_code, video_codec, false AS has_summary
FROM done;
