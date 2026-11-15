-- The bytes of a picture, replaced with it. Params: asset id, company, bytes.
INSERT INTO public.media_asset_data (asset_id, client_id, data)
VALUES (%s, %s, %s)
ON CONFLICT (asset_id) DO UPDATE SET data = EXCLUDED.data;
