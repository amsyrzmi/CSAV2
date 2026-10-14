UPDATE public.clients
SET deployment_mode = %s,
    config = jsonb_set(
        jsonb_set(
            COALESCE(config, '{}'::jsonb),
            '{policy}',
            COALESCE(config->'policy', '{}'::jsonb),
            true
        ),
        '{policy,deployment_mode}',
        to_jsonb(%s::text),
        true
    )
WHERE id = %s
RETURNING id, deployment_mode, config;
