-- The cancel the bot last asked in this chat, while the message being answered (on %(trace_id)s)
-- can still be the answer:
-- - the customer was sent it as drafted, and sent_at says when: the asking turn's own reply went
--   out with no approval (its key is `<trace>:0`), or staff approved that draft unchanged (the
--   approval's message, `appr:<id>:approved`). A draft still held, rejected, edited or expired
--   never reached the customer as asked, so it is no question at all;
-- - the customer has sent nothing else in the chat since the message that asked it. Any message in
--   between drops the question.
-- A question asked with no chat (a direct call, never a customer turn) is answered only with no
-- chat, and has no sent_at. The age (from sent_at) and the booking's version are the caller's.
-- Served by booking_turn_actions_questions (client_id, conversation_id, created_at, turn_key).
SELECT q.booking_id, q.booking_version, q.expires_at, q.conversation_id, sent.sent_at
FROM (
    SELECT a.booking_id, a.booking_version, a.expires_at, a.conversation_id, a.trace_id
    FROM public.booking_turn_actions a
    JOIN public.bookings b ON b.client_id = a.client_id AND b.id = a.booking_id
    WHERE a.client_id = %(client_id)s AND a.action = 'ask_cancel'
      AND (a.conversation_id = %(conversation_id)s::uuid
           OR (%(conversation_id)s::uuid IS NULL AND a.conversation_id IS NULL))
      AND b.contact_id = %(contact_id)s
    ORDER BY a.created_at DESC, a.turn_key DESC
    LIMIT 1
) AS q
LEFT JOIN LATERAL (
    SELECT min(m.created_at) AS sent_at
    FROM public.messages m
    WHERE m.client_id = %(client_id)s AND m.direction = 'outbound'
      AND m.trace_id = q.trace_id
      AND m.idempotency_key IN (
            q.trace_id::text || ':0',
            (SELECT 'appr:' || ap.id || ':approved'
             FROM public.approvals ap
             WHERE ap.client_id = %(client_id)s AND ap.trace_id = q.trace_id
               AND ap.status = 'approved'))
) AS sent ON true
WHERE (q.conversation_id IS NULL OR sent.sent_at IS NOT NULL)
  AND NOT EXISTS (
    SELECT 1
    FROM public.messages m
    WHERE m.client_id = %(client_id)s AND m.conversation_id = q.conversation_id
      AND m.direction = 'inbound'
      AND m.trace_id IS DISTINCT FROM q.trace_id
      AND m.trace_id IS DISTINCT FROM %(trace_id)s::uuid
      AND m.created_at > coalesce(
            (SELECT max(asked.created_at)
             FROM public.messages asked
             WHERE asked.client_id = %(client_id)s AND asked.conversation_id = q.conversation_id
               AND asked.direction = 'inbound' AND asked.trace_id = q.trace_id),
            '-infinity'::timestamptz));
