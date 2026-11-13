-- Read under the company lock, so two uploads cannot both take the last slot. PDFs only: videos
-- are counted apart by video_usage.sql.
SELECT count(*)::int AS items, coalesce(sum(byte_size), 0)::bigint AS bytes
FROM public.training_items
WHERE client_id = %s AND kind = 'pdf';
