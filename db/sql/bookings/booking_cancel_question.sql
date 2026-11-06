-- The cancel the bot last asked this customer to confirm, while the message being answered (on
-- %(trace_id)s) can still be the answer: it was asked in this chat, and the customer has sent
-- nothing else there since the message that asked it. Any message in between drops the question.
-- A question asked with no chat (a direct call) is answered only with no chat. Its age and the
-- booking's version are the caller's to check.
SELECT q.booking_id, q.booking_version, q.expires_at
FROM (
    SELECT a.booking_id, a.booking_version, a.expires_at, a.conversation_id, a.trace_id
    FROM public.booking_turn_actions a
    JOIN public.bookings b ON b.client_id = a.client_id AND b.id = a.booking_id
    WHERE a.client_id = %(client_id)s AND a.action = 'ask_cancel'
      AND b.contact_id = %(contact_id)s
    ORDER BY a.created_at DESC, a.turn_key DESC
    LIMIT 1
) AS q
WHERE q.conversation_id IS NOT DISTINCT FROM %(conversation_id)s::uuid
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
