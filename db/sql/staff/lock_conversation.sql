SELECT c.id, c.client_id, c.contact_id, c.channel, c.channel_thread, c.status,
       c.owned_by, c.ai_muted_until, c.ai_suppressed_before,
       (c.owned_by = 'human_support' AND
        (c.ai_muted_until IS NULL OR c.ai_muted_until > now())) IS TRUE AS human_muted
FROM public.conversations c
WHERE c.client_id = %s AND c.id = %s
FOR UPDATE OF c;
