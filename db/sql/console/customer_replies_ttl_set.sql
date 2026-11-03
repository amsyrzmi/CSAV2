UPDATE public.clients
SET config = jsonb_set(config, '{policy,approval_ttl_minutes}', to_jsonb(%s::int), true)
WHERE id = %s;
