UPDATE public.clients
SET config = jsonb_set(
        jsonb_set(
            COALESCE(config, '{}'::jsonb),
            '{policy}',
            COALESCE(config->'policy', '{}'::jsonb),
            true
        ),
        '{policy,decide_mode}',
        to_jsonb(%s::text),
        true
    )
WHERE id = %s
RETURNING id, config -> 'policy' ->> 'decide_mode' AS decide_mode;
