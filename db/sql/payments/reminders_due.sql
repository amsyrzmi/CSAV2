-- A page of the company's invoices owed a payment reminder, oldest due first, after the keyset
-- (after_due, after_id). Only open invoices a customer can pay (issued, overdue) with a contact, a
-- reference and a due date, and not for a period its subscription will not run (the subscription
-- cancels at period end and the invoice's period starts at or after renews_at). A step (days from
-- due_at, from payment_settings.reminder_days) is owed once due_at + step days <= now; an opening
-- invoice (a subscription's, no period_start yet) owes its earliest step from issued_at, so the
-- first notice with how to pay goes when it is issued whatever lead_days and the steps are. An
-- invoice is listed when at least one owed step has no invoice_reminders row yet. newest is the
-- latest owed step and newest_open says it has no row; older are the earlier owed steps without a
-- row (the job records them skipped: never sent late).
-- subscription_id never changes once set: the job locks that subscription before the invoice.
SELECT i.id, i.due_at, i.subscription_id, owed.steps[1] AS newest,
       NOT EXISTS (SELECT 1 FROM public.invoice_reminders r
                    WHERE r.client_id = i.client_id AND r.invoice_id = i.id
                      AND r.step = owed.steps[1]) AS newest_open,
       coalesce(ARRAY(SELECT o.step FROM unnest(owed.steps[2:]) AS o(step)
                       WHERE NOT EXISTS (SELECT 1 FROM public.invoice_reminders r
                                          WHERE r.client_id = i.client_id
                                            AND r.invoice_id = i.id AND r.step = o.step)
                       ORDER BY o.step), '{}') AS older
FROM public.invoices i
CROSS JOIN LATERAL (
    SELECT array_agg(s.step ORDER BY s.step DESC) AS steps
    FROM unnest(%(steps)s::int[]) AS s(step)
    WHERE i.due_at + make_interval(days => s.step) <= %(now)s
       OR (i.subscription_id IS NOT NULL AND i.period_start IS NULL
           AND s.step = (SELECT min(m.step) FROM unnest(%(steps)s::int[]) AS m(step))
           AND i.issued_at <= %(now)s)
) owed
WHERE i.client_id = %(client_id)s
  AND i.status IN ('issued', 'overdue')
  AND i.contact_id IS NOT NULL AND i.reference IS NOT NULL AND i.due_at IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM public.subscriptions s
                   WHERE s.client_id = i.client_id AND s.id = i.subscription_id
                     AND s.cancel_at_period_end AND s.renews_at IS NOT NULL
                     AND i.period_start >= s.renews_at)
  AND (%(after_due)s::timestamptz IS NULL
       OR (i.due_at, i.id) > (%(after_due)s::timestamptz, %(after_id)s::text))
  AND EXISTS (SELECT 1 FROM unnest(owed.steps) AS o(step)
               WHERE NOT EXISTS (SELECT 1 FROM public.invoice_reminders r
                                  WHERE r.client_id = i.client_id AND r.invoice_id = i.id
                                    AND r.step = o.step))
ORDER BY i.due_at, i.id
LIMIT %(batch_size)s;
