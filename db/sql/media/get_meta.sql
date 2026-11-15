-- A picture's metadata by id, in one company; no bytes. Params: company, asset id.
SELECT id, kind, subject_key, media_type, sha256
FROM public.media_assets
WHERE client_id = %s AND id = %s;
