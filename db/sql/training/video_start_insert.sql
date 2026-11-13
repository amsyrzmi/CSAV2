-- Adds a video and its open upload in one statement, under the company lock (the caller has
-- checked the idempotency key and the quotas first). byte_size is the declared size, reserved
-- against the company's video bytes until completion records what arrived; no sha256 until then.
-- The upload row references the item inserted by the same statement.
WITH item AS (
    INSERT INTO public.training_items
        (id, client_id, kind, title, storage_name, original_name, media_type, byte_size,
         created_by, request_key, fingerprint, status, container)
    VALUES
        (%(id)s, %(client_id)s, 'video', %(title)s, %(storage_name)s, %(original_name)s,
         %(media_type)s, %(byte_size)s, %(created_by)s, %(request_key)s, %(fingerprint)s,
         'uploading', %(container)s)
    RETURNING *
), upload AS (
    INSERT INTO public.training_video_uploads
        (client_id, item_id, upload_id, piece_size, piece_count, declared_bytes)
    SELECT client_id, id, %(upload_id)s, %(piece_size)s, %(piece_count)s, byte_size FROM item
    RETURNING item_id
)
SELECT i.id, i.kind, i.title, i.description, i.category, i.view_mode, i.page_count, i.byte_size,
       i.created_at, i.created_by, i.status, i.duration_ms, i.failure_code, i.video_codec,
       false AS has_summary
FROM item i
JOIN upload u ON u.item_id = i.id;
