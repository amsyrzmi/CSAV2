SELECT
    brand_name,
    brand_tone,
    config -> 'composer' AS composer,
    config -> 'persona' AS persona
FROM public.clients
WHERE id = %s;
