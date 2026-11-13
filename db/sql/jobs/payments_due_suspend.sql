-- Step 4 of the payments_due job, second statement: the subscriptions payments_due_suspend_lock.sql
-- claimed and locked (%(ids)s) that are still active and still have an overdue invoice due before
-- %(cutoff)s (read here with this statement's own, later snapshot) are paused for being unpaid:
-- status 'paused', paused_at = %(now)s, pause_reason 'unpaid', resume_at cleared (so rule 3 of the
-- subscriptions_due job never resumes it by date; paying the overdue invoices does, in
-- csa.payments.invoices.mark_paid), version + 1. Each gets one 'suspended_unpaid' timeline event
-- by 'system:payments_due' with {invoice_id}: the longest overdue of those invoices. Nothing is
-- charged and nobody is messaged. %(now)s is the job clock.
WITH due AS (
    SELECT s.id, s.status AS from_status,
           (SELECT i.id FROM public.invoices i
             WHERE i.client_id = s.client_id AND i.subscription_id = s.id
               AND i.status = 'overdue' AND i.due_at < %(cutoff)s::timestamptz
             ORDER BY i.due_at, i.id
             LIMIT 1) AS invoice_id
    FROM public.subscriptions s
    WHERE s.client_id = %(client_id)s
      AND s.id = ANY(%(ids)s::uuid[])
      AND s.status = 'active'
    FOR UPDATE OF s
), changed AS (
    UPDATE public.subscriptions s
    SET status = 'paused', paused_at = %(now)s::timestamptz, pause_reason = 'unpaid',
        resume_at = NULL, version = s.version + 1
    FROM due
    WHERE s.client_id = %(client_id)s AND s.id = due.id AND due.invoice_id IS NOT NULL
    RETURNING s.id, due.from_status, s.status AS to_status, due.invoice_id
), logged AS (
    INSERT INTO public.subscription_events
        (client_id, subscription_id, actor, action, from_status, to_status, payload)
    SELECT %(client_id)s, id, 'system:payments_due', 'suspended_unpaid', from_status, to_status,
           jsonb_build_object('invoice_id', invoice_id)
    FROM changed
    RETURNING id
)
SELECT count(*) AS suspended FROM logged;
