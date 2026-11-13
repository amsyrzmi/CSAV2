-- The pieces of an upload recorded so far, in order.
SELECT number, byte_size, sha256, etag
FROM public.training_video_pieces
WHERE client_id = %(client_id)s AND item_id = %(item_id)s
ORDER BY number;
