WITH candidate AS (
    SELECT event.id
    FROM public.ingress_events AS event
    WHERE event.status = 'received'
      AND event.next_attempt_at <= now()
      AND event.raw IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM public.ingress_events AS earlier
          WHERE earlier.client_id = event.client_id
            AND earlier.raw #>> '{envelope,channel}'
                = event.raw #>> '{envelope,channel}'
            AND earlier.raw #>> '{envelope,conversation,channel_thread}'
                = event.raw #>> '{envelope,conversation,channel_thread}'
            AND (earlier.created_at, earlier.id) < (event.created_at, event.id)
            AND earlier.status IN ('received', 'dispatched', 'failed')
      )
    ORDER BY event.created_at, event.id
    LIMIT 1
    FOR UPDATE OF event SKIP LOCKED
)
UPDATE public.ingress_events AS event
SET status = 'dispatched',
    dispatched_at = now(),
    attempts = event.attempts + 1
FROM candidate
WHERE event.id = candidate.id
RETURNING event.id,
          event.client_id,
          event.endpoint_id,
          event.trace_id,
          event.raw,
          event.attempts;
