SELECT endpoint_id, client_id, credential_id, reply_mode, sync_wait_ms,
       is_enabled, disabled_reason
FROM public.resolve_endpoint(%s)
