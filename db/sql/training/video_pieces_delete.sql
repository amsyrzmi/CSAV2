-- Forget pieces the store found changed or missing at completion, so they are sent again.
DELETE FROM public.training_video_pieces
WHERE client_id = %(client_id)s AND item_id = %(item_id)s AND number = ANY(%(numbers)s::int[]);
