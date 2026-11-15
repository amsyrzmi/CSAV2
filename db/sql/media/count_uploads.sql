-- The pictures a company uploaded (generated pictures do not count toward the limit).
SELECT count(*) AS n
FROM public.media_assets
WHERE client_id = %s AND source = 'upload';
