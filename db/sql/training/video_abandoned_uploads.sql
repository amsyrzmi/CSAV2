-- Uploads with no new piece for UPLOAD_ABANDON_SECONDS (or none since they started): the item goes,
-- its upload and piece rows by cascade. Run under the company lock. The upload rows are locked
-- first and their age checked again after any piece being recorded commits, so an upload that just
-- received a piece stays. The caller audits each and aborts the store's upload after commit.
WITH idle AS (
    SELECT u.item_id, u.upload_id
    FROM public.training_video_uploads u
    WHERE u.client_id = %(client_id)s
      AND coalesce(u.last_piece_at, u.created_at)
          < now() - make_interval(secs => %(abandon_seconds)s)
    FOR UPDATE
)
DELETE FROM public.training_items i
USING idle
WHERE i.client_id = %(client_id)s AND i.id = idle.item_id AND i.status = 'uploading'
RETURNING i.id, i.storage_name, idle.upload_id;
