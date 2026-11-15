-- Removes a picture; its bytes go with it (cascade). Params: company, asset id.
DELETE FROM public.media_assets
WHERE client_id = %s AND id = %s
RETURNING id;
