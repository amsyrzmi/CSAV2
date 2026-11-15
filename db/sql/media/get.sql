-- A picture and its bytes by id, in one company. Params: company, asset id.
SELECT a.id, a.kind, a.subject_key, a.media_type, a.sha256, d.data
FROM public.media_assets a
JOIN public.media_asset_data d ON d.asset_id = a.id AND d.client_id = a.client_id
WHERE a.client_id = %s AND a.id = %s;
