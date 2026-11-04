-- One customer conversation with its contact fields. Named parameters: client_id, conversation_id.
SELECT c.id, c.channel, c.status, c.owned_by, c.ai_muted_until, c.inbox_version,
       c.opened_at,
       (c.owned_by = 'human_support' AND
        (c.ai_muted_until IS NULL OR c.ai_muted_until > now())) IS TRUE AS human_muted,
       k.full_name, k.phone, k.telegram_username, k.contact_kind AS kind, kn.known,
       lm.body AS last_body, lm.created_at AS last_at, pa.n AS pending_approvals
FROM public.conversations c
JOIN public.contacts k ON k.client_id = c.client_id AND k.id = c.contact_id
LEFT JOIN LATERAL (
    SELECT coalesce(m.redacted_body, m.body) AS body, m.created_at
    FROM public.messages m
    WHERE m.client_id = c.client_id AND m.conversation_id = c.id
    ORDER BY m.created_at DESC, m.id DESC LIMIT 1
) lm ON true
CROSS JOIN LATERAL (
    SELECT (EXISTS (SELECT 1 FROM public.orders o
                    WHERE o.client_id = c.client_id AND o.contact_id = c.contact_id)
         OR EXISTS (SELECT 1 FROM public.subscriptions s
                    WHERE s.client_id = c.client_id AND s.contact_id = c.contact_id)
         OR nullif(btrim(k.email), '') IS NOT NULL
         OR nullif(btrim(k.address), '') IS NOT NULL) AS known
) kn
CROSS JOIN LATERAL (
    SELECT count(*)::int AS n FROM public.approvals a
    WHERE a.client_id = c.client_id AND a.conversation_id = c.id
      AND a.status = 'pending' AND a.expires_at > now()
) pa
WHERE c.client_id = %(client_id)s AND c.id = %(conversation_id)s
  AND c.channel IN ('telegram','whatsapp')
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id)
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p WHERE p.contact_id = c.contact_id);
