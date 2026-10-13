SELECT e.id AS endpoint_id, e.rate_limit_rpm, e.is_enabled, e.disabled_reason,
       c.id AS credential_id, c.channel, c.access_token, c.meta
FROM public.webhook_endpoints AS e
JOIN public.channel_credentials AS c
  ON c.id = e.credential_id
 AND c.client_id = e.client_id
WHERE e.id = %s
  AND e.client_id = %s
