SELECT c.clarify_count,
       (c.owned_by = 'human_support' AND
        (c.ai_muted_until IS NULL OR c.ai_muted_until > now())) IS TRUE AS human_muted,
       c.summary,
       ARRAY(
           SELECT o.id FROM public.orders o
           WHERE o.client_id = c.client_id AND o.contact_id = c.contact_id
             AND o.status IN ('pending_confirmation', 'confirmed', 'processing', 'shipped')
           ORDER BY o.created_at DESC
           LIMIT 10
       ) AS active_order_ids
FROM public.conversations c
WHERE c.id = %s
  AND c.client_id = %s;
