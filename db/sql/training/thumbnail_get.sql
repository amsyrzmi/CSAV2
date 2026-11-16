-- An item's visibility facts and its thumbnail's metadata, never the bytes (the route decides who
-- may see it and answers a revalidation from this alone). Params: company, item id. No row: no such
-- item in this company; thumbnail_id NULL: the item has no thumbnail.
SELECT i.id, i.kind, i.status, t.id AS thumbnail_id, t.media_type, t.sha256
FROM public.training_items i
LEFT JOIN public.media_assets t
       ON t.client_id = i.client_id AND t.kind = 'training_thumbnail' AND t.subject_key = i.id::text
WHERE i.client_id = %s AND i.id = %s;
