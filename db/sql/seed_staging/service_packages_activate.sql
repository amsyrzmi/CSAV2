-- Params: client_id, activated_by (platform admin), reference, package keys (text[]).
INSERT INTO public.console_service_packages
    (client_id, package, status, activated_by, activated_at, reference)
SELECT %s, package, 'active', %s, now(), %s
FROM unnest(%s::text[]) AS package
ON CONFLICT (client_id, package) DO NOTHING;
