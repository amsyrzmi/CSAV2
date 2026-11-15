-- Removes the picture of one subject; its bytes go with it (cascade). Params: company, kind, key.
DELETE FROM public.media_assets
WHERE client_id = %s AND kind = %s AND subject_key = %s
RETURNING id;
