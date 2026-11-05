-- What a booking email says about the company: the name customers know it by, and its time zone.
SELECT coalesce(nullif(btrim(brand_name), ''), name) AS brand, timezone
FROM public.clients
WHERE id = %(client_id)s;
