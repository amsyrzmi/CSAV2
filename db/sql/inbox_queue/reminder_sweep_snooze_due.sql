-- The inbox_reminders_sweep's snooze read (plan 2a, decisions 1 to 3 and 12): the company's
-- snoozed items with no inbox.snooze_due reminder for the current snooze (those snoozed before
-- this reminder existed; snooze() schedules its own). The cycle key is
-- 'inbox.snooze_due:<conversation id>:<snoozed_until in epoch microseconds>'; a snooze with any
-- reminder row is never scheduled again.
-- Read along conversations_queue_snoozed_idx (client_id, snoozed_until) WHERE snoozed, soonest
-- wake first, no sort, stopping at the limit parameter; the reminder check is one probe of the
-- (client_id, dedupe_key) key per row, kept a per-row filter by OFFSET 0.
-- Named parameters: client_id, limit (the others of the sweep are accepted and unused).
SELECT c.id, c.snoozed_until AS cycle
FROM public.conversations c
WHERE c.client_id = %(client_id)s
  AND c.queue_status = 'snoozed'
  AND c.snoozed_until IS NOT NULL
  AND c.channel IN ('telegram', 'whatsapp')
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id OFFSET 0)
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p
                  WHERE p.contact_id = c.contact_id OFFSET 0)
  AND NOT EXISTS (SELECT 1 FROM public.reminders r
                  WHERE r.client_id = c.client_id
                    AND r.dedupe_key = 'inbox.snooze_due:' || c.id::text || ':'
                        || (extract(epoch FROM c.snoozed_until) * 1000000)::bigint::text
                  OFFSET 0)
ORDER BY c.snoozed_until
LIMIT %(limit)s;
