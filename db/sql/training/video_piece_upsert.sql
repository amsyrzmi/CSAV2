-- Record a stored piece only while its upload is open: the insert reads from the upload row it
-- has just touched (last_piece_at), so a removal or a completion that deleted the row (or holds it
-- locked until it does) leaves nothing recorded and returns no row. A piece sent again replaces
-- the earlier record.
WITH open AS (
    UPDATE public.training_video_uploads
    SET last_piece_at = now()
    WHERE client_id = %(client_id)s AND item_id = %(item_id)s
    RETURNING client_id, item_id
)
INSERT INTO public.training_video_pieces (client_id, item_id, number, byte_size, sha256, etag)
SELECT client_id, item_id, %(number)s, %(byte_size)s, %(sha256)s, %(etag)s FROM open
ON CONFLICT (client_id, item_id, number) DO UPDATE
SET byte_size = EXCLUDED.byte_size,
    sha256 = EXCLUDED.sha256,
    etag = EXCLUDED.etag,
    recorded_at = now()
RETURNING number;
