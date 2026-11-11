-- ladder_stale: the chat's newest earlier message (not this turn's own) is older than
-- %(stale_after)s, so the unresolved-turns count starts over. No earlier message: not stale.
SELECT c.clarify_count,
       COALESCE((
           SELECT max(m.created_at) < now() - %(stale_after)s
           FROM public.messages m
           WHERE m.client_id = c.client_id AND m.conversation_id = c.id
             AND (m.trace_id IS NULL OR m.trace_id <> %(trace_id)s)
       ), false) AS ladder_stale,
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
WHERE c.id = %(conversation_id)s
  AND c.client_id = %(client_id)s;
