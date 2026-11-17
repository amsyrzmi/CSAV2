-- Customer Inbox queue list, highest priority first.
-- One of four files (list_queue_activity, _waiting, _due, _priority) that differ only in the
-- keyset condition and the ORDER BY: the filter block between the "queue filter" marker comments
-- is the same text in all four (tests/test_inbox_queue_api.py checks it), so a filter change
-- goes into each of them.
-- Named parameters (NULL or false means "no filter"):
--   client_id; channel; states, priorities, reasons, topics (text arrays); assignee (uuid; the
--   actor's id for "me"); unassigned (bool); overdue (bool); waiting_over (minutes); needs_review
--   (bool: a live pending approval); sender ('known' or 'new'); like (escaped ILIKE pattern) and
--   digits (the query's digits) for the search; after_at, after_id, after_rank (the cursor row;
--   after_id NULL on the first page); limit.
-- Shape: the inner query picks one page of conversations from public.conversations alone,
--   reading contacts only inside the search and sender conditions. The contact fields, the last
--   message, `known` and the pending approval count are then read for the page's rows only.
-- Order: priority rank (urgent 4 to low 1) descending, then due_at ascending with rows
--   without one last, then id ascending. after_rank is the cursor row's rank. No index
--   holds the rank, so this order sorts the rows in scope (a top-N sort). The API allows it
--   with the state filter needs_staff alone, so those are the needs_staff rows only.
-- Exclusions: the two NOT EXISTS carry OFFSET 0, an optimisation fence and not a limit. It
--   keeps them as per-row filters (an index probe each) on the queue index scan; without it the
--   planner may turn them into hash anti joins, whose output is not ordered, and then sort.
-- Search: a bounded per-company scan of contacts on their client_id index. The trigram
--   indexes (0620) cannot serve it: csa_app reads contacts under row security and the ILIKE and
--   LIKE operators are not leakproof, so the planner cannot use an index on the matched columns.
SELECT p.id, p.channel, p.status, p.owned_by, p.ai_muted_until, p.inbox_version, p.opened_at,
       p.human_muted, p.queue_status, p.queue_reason, p.topic, p.priority, p.assignee_id,
       p.claimed_at, p.needs_staff_since, p.due_at, p.snoozed_until, p.last_customer_at,
       p.last_staff_reply_at, p.last_activity_at,
       k.full_name, k.phone, k.telegram_username, k.contact_kind AS kind, kn.known,
       lm.body AS last_body, lm.created_at AS last_at, pa.n AS pending_approvals
FROM (
    SELECT c.id, c.client_id, c.contact_id, c.channel, c.status, c.owned_by, c.ai_muted_until,
           c.inbox_version, c.opened_at,
           (c.owned_by = 'human_support' AND
            (c.ai_muted_until IS NULL OR c.ai_muted_until > now())) IS TRUE AS human_muted,
           c.queue_status, c.queue_reason, c.topic, c.priority, c.assignee_user_id AS assignee_id,
           c.claimed_at, c.needs_staff_since, c.due_at, c.snoozed_until, c.last_customer_at,
           c.last_staff_reply_at, c.last_activity_at,
           array_position(ARRAY['low', 'normal', 'high', 'urgent'], c.priority) AS priority_rank
    FROM public.conversations c
    -- queue filter: begin
    WHERE c.client_id = %(client_id)s
      -- The exclusions: customer channels only, and never a console chat conversation.
      AND c.channel IN ('telegram', 'whatsapp')
      AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id OFFSET 0)
      AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p
                      WHERE p.contact_id = c.contact_id OFFSET 0)
      AND (%(channel)s::text IS NULL OR c.channel = %(channel)s::text)
      AND (%(states)s::text[] IS NULL OR c.queue_status = ANY (%(states)s::text[]))
      AND (%(assignee)s::uuid IS NULL OR c.assignee_user_id = %(assignee)s::uuid)
      AND (NOT %(unassigned)s::boolean OR c.assignee_user_id IS NULL)
      AND (%(priorities)s::text[] IS NULL OR c.priority = ANY (%(priorities)s::text[]))
      AND (%(reasons)s::text[] IS NULL OR c.queue_reason = ANY (%(reasons)s::text[]))
      AND (%(topics)s::text[] IS NULL OR c.topic = ANY (%(topics)s::text[]))
      AND (NOT %(overdue)s::boolean OR (c.queue_status = 'needs_staff' AND c.due_at < now()))
      AND (%(waiting_over)s::integer IS NULL
           OR (c.queue_status = 'needs_staff'
               AND c.needs_staff_since < now() - make_interval(mins => %(waiting_over)s::integer)))
      AND (NOT %(needs_review)s::boolean OR EXISTS (
          SELECT 1 FROM public.approvals a
          WHERE a.client_id = c.client_id AND a.conversation_id = c.id
            AND a.status = 'pending' AND a.expires_at > now()))
      -- `known` cannot be stored on the row (orders, subscriptions and the contact change it), so
      -- with this filter it is computed for every row the scan reads, until one page is full.
      AND (%(sender)s::text IS NULL OR EXISTS (
          SELECT 1 FROM public.contacts k
          WHERE k.client_id = c.client_id AND k.id = c.contact_id
            AND (EXISTS (SELECT 1 FROM public.orders o
                         WHERE o.client_id = c.client_id AND o.contact_id = c.contact_id)
                 OR EXISTS (SELECT 1 FROM public.subscriptions s
                            WHERE s.client_id = c.client_id AND s.contact_id = c.contact_id)
                 OR nullif(btrim(k.email), '') IS NOT NULL
                 OR nullif(btrim(k.address), '') IS NOT NULL) = (%(sender)s::text = 'known')))
      AND (%(like)s::text IS NULL OR EXISTS (
          SELECT 1
          FROM public.contacts k
          CROSS JOIN LATERAL (
              SELECT d.digits, regexp_replace(d.digits, '^0+', '') AS national
              FROM (SELECT regexp_replace(coalesce(k.phone, ''), '[^0-9]', '', 'g') AS digits) d
          ) ph
          WHERE k.client_id = c.client_id AND k.id = c.contact_id
            AND (k.full_name ILIKE %(like)s::text ESCAPE '\'
                 OR k.telegram_username ILIKE %(like)s::text ESCAPE '\'
                 -- phone_digits is the stored number with a leading 0 read as 60, so it ends
                 -- with the stored digits: containing the query digits implies it contains
                 -- them too. The second test keeps the meaning of a contains match on the
                 -- stored digits ("60" does not find a number stored as 012...).
                 OR (k.phone_digits LIKE '%%' || %(digits)s::text || '%%'
                     AND ph.digits LIKE '%%' || %(digits)s::text || '%%')
                 -- A number stored in local form (0125550148) is found by its international
                 -- form (60125550148): the stored digits without leading zeros, at least
                 -- seven of them, must end the query digits; phone_digits then ends with the
                 -- query's last seven digits.
                 OR (length(%(digits)s::text) >= 7
                     AND k.phone_digits LIKE '%%' || right(%(digits)s::text, 7)
                     AND length(ph.national) >= 7
                     AND right(%(digits)s::text, length(ph.national)) = ph.national))))
    -- queue filter: end
      -- Keyset: the rows after the cursor row in the ORDER BY below, written out because the
      -- directions differ (rank descending, due time and id ascending, due time NULLS LAST).
      AND (%(after_id)s::uuid IS NULL
           OR array_position(ARRAY['low', 'normal', 'high', 'urgent'], c.priority)
              < %(after_rank)s::integer
           OR (array_position(ARRAY['low', 'normal', 'high', 'urgent'], c.priority)
               = %(after_rank)s::integer
               AND ((%(after_at)s::timestamptz IS NOT NULL
                     AND ((c.due_at, c.id) > (%(after_at)s::timestamptz, %(after_id)s::uuid)
                          OR c.due_at IS NULL))
                    OR (%(after_at)s::timestamptz IS NULL
                        AND c.due_at IS NULL AND c.id > %(after_id)s::uuid))))
    ORDER BY priority_rank DESC, c.due_at ASC NULLS LAST, c.id ASC
    LIMIT %(limit)s
) p
JOIN public.contacts k ON k.client_id = p.client_id AND k.id = p.contact_id
LEFT JOIN LATERAL (
    SELECT coalesce(m.redacted_body, m.body) AS body, m.created_at
    FROM public.messages m
    WHERE m.client_id = p.client_id AND m.conversation_id = p.id
    ORDER BY m.created_at DESC, m.id DESC LIMIT 1
) lm ON true
CROSS JOIN LATERAL (
    SELECT (EXISTS (SELECT 1 FROM public.orders o
                    WHERE o.client_id = p.client_id AND o.contact_id = p.contact_id)
         OR EXISTS (SELECT 1 FROM public.subscriptions s
                    WHERE s.client_id = p.client_id AND s.contact_id = p.contact_id)
         OR nullif(btrim(k.email), '') IS NOT NULL
         OR nullif(btrim(k.address), '') IS NOT NULL) AS known
) kn
CROSS JOIN LATERAL (
    SELECT count(*)::int AS n FROM public.approvals a
    WHERE a.client_id = p.client_id AND a.conversation_id = p.id
      AND a.status = 'pending' AND a.expires_at > now()
) pa
ORDER BY p.priority_rank DESC, p.due_at ASC NULLS LAST, p.id ASC;
