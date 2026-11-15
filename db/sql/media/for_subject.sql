-- The picture of one subject (metadata only: no bytes). Params: company, kind, subject key.
SELECT id, sha256, source
FROM public.media_assets
WHERE client_id = %s AND kind = %s AND subject_key = %s;
