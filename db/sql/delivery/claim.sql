WITH candidate AS (
    SELECT delivery.id
    FROM public.outbound_deliveries AS delivery
    WHERE delivery.status = 'pending'
      AND delivery.next_attempt_at <= now()
      AND NOT EXISTS (
          SELECT 1 FROM public.outbound_deliveries earlier
          WHERE earlier.client_id = delivery.client_id
            AND earlier.conversation_id = delivery.conversation_id
            AND earlier.status IN ('pending', 'delivering')
            AND (earlier.created_at, earlier.id) < (delivery.created_at, delivery.id)
      )
    ORDER BY delivery.next_attempt_at, delivery.created_at, delivery.id
    LIMIT 1
    FOR UPDATE OF delivery SKIP LOCKED
)
UPDATE public.outbound_deliveries AS delivery
SET status = 'delivering',
    locked_at = now(),
    attempts = delivery.attempts + 1
FROM candidate
WHERE delivery.id = candidate.id
RETURNING delivery.id,
          delivery.client_id,
          delivery.endpoint_id,
          delivery.trace_id,
          delivery.conversation_id,
          delivery.message_id,
          delivery.attempts;
