-- The inbox_reminders_sweep's overdue read (plan 2a, decisions 1 to 3): the company's needs-us
-- items whose inbox.overdue reminder for the current cycle is missing, or is still unfired and
-- due at the wrong time after a priority change. csa.inbox_queue.reminders.schedule_missing
-- schedules (or moves) each row it returns, so a returned row is not returned again.
-- The cycle key is 'inbox.overdue:<conversation id>:<needs_staff_since in epoch seconds>'
-- (csa.inbox_queue.reminders.reminder_key); a cycle with any reminder row (scheduled, fired,
-- cancelled or done) is never scheduled again.
-- The due time is the item's due_at while that is in the future, else the now parameter plus the
-- response target of its priority (a fresh clock, no burst for old items). An unfired reminder
-- moves when the item's due_at is in the future and differs, or when it is past and the fresh
-- clock of the item's priority is sooner than the reminder (a priority rise on an old item);
-- otherwise it stays.
-- Read along conversations_queue_due_idx (client_id, due_at, id) WHERE needs_staff: no sort, and
-- the scan stops at the limit parameter. The reminder is one probe of the (client_id, dedupe_key)
-- key per row: the OFFSET 0 in the lateral keeps it a per-row subquery (without it the planner
-- joins it in as a hash join over every reminder of the company and then sorts). The exclusions
-- are the queue's (inbox/list_queue_due.sql), OFFSET 0 keeping them per-row filters too.
-- Named parameters: client_id, now, limit, target_urgent, target_high, target_normal, target_low.
SELECT c.id, c.priority, c.needs_staff_since AS cycle, c.due_at
FROM public.conversations c
LEFT JOIN LATERAL (
    SELECT r.id, r.due_at, r.state, r.fire_count
    FROM public.reminders r
    WHERE r.client_id = c.client_id
      AND r.dedupe_key = 'inbox.overdue:' || c.id::text || ':'
          || floor(extract(epoch FROM c.needs_staff_since))::bigint::text
    OFFSET 0
) r ON true
WHERE c.client_id = %(client_id)s
  AND c.queue_status = 'needs_staff'
  AND c.channel IN ('telegram', 'whatsapp')
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id OFFSET 0)
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p
                  WHERE p.contact_id = c.contact_id OFFSET 0)
  AND (r.id IS NULL
       OR (r.state = 'scheduled' AND r.fire_count = 0
           AND CASE WHEN c.due_at > %(now)s::timestamptz THEN r.due_at <> c.due_at
                    ELSE r.due_at > %(now)s::timestamptz + make_interval(mins => CASE c.priority
                        WHEN 'urgent' THEN %(target_urgent)s::int
                        WHEN 'high' THEN %(target_high)s::int
                        WHEN 'low' THEN %(target_low)s::int
                        ELSE %(target_normal)s::int END)
               END))
ORDER BY c.due_at, c.id
LIMIT %(limit)s;
