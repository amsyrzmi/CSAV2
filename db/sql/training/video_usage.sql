-- Read under the company lock, so two upload starts cannot both take the last slot or the last
-- bytes. Videos only: PDFs are counted by usage.sql. A video that failed for a permanent reason
-- has had its file deleted at once, so its bytes no longer count; every other row does, an upload
-- still arriving included. "uploading" is the number of uploads open (not of rows).
SELECT count(*)::int AS items,
       coalesce(sum(byte_size) FILTER (
           WHERE NOT (status = 'failed' AND failure_code IN
               ('training_video_unreadable', 'training_video_too_long', 'training_video_codec'))
       ), 0)::bigint AS bytes,
       (SELECT count(*)::int FROM public.training_video_uploads
         WHERE client_id = %(client_id)s) AS uploading
FROM public.training_items
WHERE client_id = %(client_id)s AND kind = 'video';
