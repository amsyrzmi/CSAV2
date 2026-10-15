INSERT INTO public.clients (id, slug, name, timezone, platform_version, config)
VALUES (%s, %s, %s, %s, 'csav2', '{"runtime":"langgraph","policy":{"deployment_mode":"shadow"}}'::jsonb)
RETURNING id, slug, name, timezone, status;
