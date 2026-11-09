UPDATE public.clients
SET brand_tone = %s,
    config = jsonb_set(coalesce(config, '{}'::jsonb), '{persona}', %s, true),
    updated_at = now()
WHERE id = %s;
