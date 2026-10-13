INSERT INTO public.clients (slug, name, platform_version, status, deployment_mode)
VALUES (%s, %s, 'lg03-local-dev', 'pilot', 'shadow')
ON CONFLICT (slug) DO NOTHING
RETURNING id
