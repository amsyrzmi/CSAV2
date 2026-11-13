-- The open upload of an item, locked: read in the removal's transaction (so a piece that is being
-- recorded waits for the delete) and in the completion's (so a removal waits for it). No row: no
-- upload was open.
SELECT u.upload_id, i.storage_name, u.piece_size, u.piece_count, u.declared_bytes
FROM public.training_video_uploads u
JOIN public.training_items i ON i.client_id = u.client_id AND i.id = u.item_id
WHERE u.client_id = %s AND u.item_id = %s
FOR UPDATE OF u;
