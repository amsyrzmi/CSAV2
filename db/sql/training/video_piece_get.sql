-- A video item, its open upload (open is false once it was completed or removed) and the
-- recorded hash of piece %(number)s (NULL number: no piece). A plain read that takes no lock: a
-- piece holds none until it records itself.
SELECT i.kind, i.status, i.storage_name, i.container,
       u.item_id IS NOT NULL AS open, u.upload_id, u.piece_size, u.piece_count, u.declared_bytes,
       p.sha256 AS recorded
FROM public.training_items i
LEFT JOIN public.training_video_uploads u ON u.client_id = i.client_id AND u.item_id = i.id
LEFT JOIN public.training_video_pieces p
       ON p.client_id = i.client_id AND p.item_id = i.id AND p.number = %(number)s
WHERE i.client_id = %(client_id)s AND i.id = %(item_id)s;
