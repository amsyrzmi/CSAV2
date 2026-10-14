UPDATE public.clients
SET config = jsonb_set(
        COALESCE(config, '{}'::jsonb),
        '{runtime}',
        to_jsonb(%s::text)
    )
WHERE id = %s
RETURNING id, config ->> 'runtime' AS runtime;
