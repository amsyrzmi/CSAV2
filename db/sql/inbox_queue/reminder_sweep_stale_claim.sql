-- The inbox_reminders_sweep's stale-claim read (plan 2a, decisions 1 to 3): the company's claimed
-- needs-us items with no inbox.stale_claim reminder for the current claim. The cycle key is
-- 'inbox.stale_claim:<conversation id>:<claimed_at in epoch microseconds>'; a claim with any
-- reminder row is never scheduled again, and a new claim (reassign) is a new cycle.
-- Read along a needs_staff partial index (the planner picks conversations_queue_mine_idx, which
-- holds the claimed rows), stopping at the limit parameter. No order: a row returned is scheduled
-- and is not returned again, so the next run reads the rest; an ORDER BY here would sort, since
-- no index holds the claimed needs-us rows in due order. The reminder check is one probe of the
-- (client_id, dedupe_key) key per row, kept a per-row filter by OFFSET 0 as the queue's
-- exclusions are (inbox/list_queue_due.sql).
-- Named parameters: client_id, limit (the others of the sweep are accepted and unused).
SELECT c.id, c.claimed_at AS cycle
FROM public.conversations c
WHERE c.client_id = %(client_id)s
  AND c.queue_status = 'needs_staff'
  AND c.assignee_user_id IS NOT NULL
  AND c.claimed_at IS NOT NULL
  AND c.channel IN ('telegram', 'whatsapp')
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id OFFSET 0)
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p
                  WHERE p.contact_id = c.contact_id OFFSET 0)
  AND NOT EXISTS (SELECT 1 FROM public.reminders r
                  WHERE r.client_id = c.client_id
                    AND r.dedupe_key = 'inbox.stale_claim:' || c.id::text || ':'
                        || (extract(epoch FROM c.claimed_at) * 1000000)::bigint::text
                  OFFSET 0)
LIMIT %(limit)s;
