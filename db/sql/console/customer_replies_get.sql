SELECT
    brand_name,
    brand_tone,
    config -> 'persona' ->> 'name' AS persona_name,
    config -> 'persona' ->> 'intro_line' AS intro_line,
    config -> 'policy' AS policy
FROM public.clients
WHERE id = %s;
