SELECT
    config ->> 'runtime' AS runtime,
    config ->> 'n8n_webhook_url' AS n8n_webhook_url,
    COALESCE(config -> 'policy' ->> 'deployment_mode', deployment_mode) AS deployment_mode
FROM public.clients
WHERE id = %s;
