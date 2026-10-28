-- Customer inbox list. Named parameters: client_id, channel, before, limit,
-- needs_review, sender ('known' | 'new' | NULL), like (escaped pattern), digits.
-- Order is last activity (last message time, else opened_at) then id, newest first.
SELECT c.id, c.channel, c.status, c.owned_by, c.ai_muted_until, c.inbox_version,
       c.opened_at,
       (c.owned_by = 'human_support' AND
        (c.ai_muted_until IS NULL OR c.ai_muted_until > now())) IS TRUE AS human_muted,
       k.full_name, k.phone, k.contact_kind AS kind, kn.known,
       lm.body AS last_body, lm.created_at AS last_at, pa.n AS pending_approvals
FROM public.conversations c
JOIN public.contacts k ON k.client_id = c.client_id AND k.id = c.contact_id
LEFT JOIN LATERAL (
    SELECT coalesce(m.redacted_body, m.body) AS body, m.created_at
    FROM public.messages m
    WHERE m.client_id = c.client_id AND m.conversation_id = c.id
    ORDER BY m.created_at DESC, m.id DESC LIMIT 1
) lm ON true
CROSS JOIN LATERAL (SELECT coalesce(lm.created_at, c.opened_at) AS at) act
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
WHERE c.client_id = %(client_id)s AND c.channel IN ('telegram','whatsapp')
  AND (%(channel)s::text IS NULL OR c.channel = %(channel)s)
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id)
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p WHERE p.contact_id = c.contact_id)
  AND (NOT %(needs_review)s::boolean OR pa.n > 0)
  AND (%(sender)s::text IS NULL OR kn.known = (%(sender)s::text = 'known'))
  AND (%(like)s::text IS NULL
       OR coalesce(k.full_name, '') ILIKE %(like)s::text ESCAPE '\'
       OR (%(digits)s::text IS NOT NULL
           AND regexp_replace(coalesce(k.phone, ''), '[^0-9]', '', 'g')
               LIKE '%%' || %(digits)s::text || '%%'))
  AND (%(before)s::uuid IS NULL OR (act.at, c.id) < (
      SELECT coalesce(bl.created_at, bc.opened_at), bc.id
      FROM public.conversations bc
      LEFT JOIN LATERAL (
          SELECT bm.created_at FROM public.messages bm
          WHERE bm.client_id = bc.client_id AND bm.conversation_id = bc.id
          ORDER BY bm.created_at DESC, bm.id DESC LIMIT 1
      ) bl ON true
      WHERE bc.client_id = c.client_id AND bc.id = %(before)s::uuid))
ORDER BY act.at DESC, c.id DESC LIMIT %(limit)s;
