-- The open upload of an item, read in the removal's transaction (locked, so a piece that is
-- being recorded waits for the delete). No row: no upload was open.
SELECT u.upload_id, i.storage_name
FROM public.training_video_uploads u
JOIN public.training_items i ON i.client_id = u.client_id AND i.id = u.item_id
WHERE u.client_id = %s AND u.item_id = %s
FOR UPDATE OF u;
