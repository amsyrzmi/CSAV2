-- Rule 1 of the subscriptions_due job: an active subscription flagged cancel_at_period_end whose
-- period has ended becomes cancelled, effective at the period end (cancelled_at = renews_at).
-- A scheduled plan change is dropped with it (pending_plan and pending_effective_at cleared, as
-- a manual cancel_now does); the event payload names it as cleared_pending_plan (NULL if none).
-- Rows are claimed FOR UPDATE SKIP LOCKED (oldest period end first, at most %(batch_size)s), the
-- change bumps version, and each row gets one 'cancelled_at_period_end' timeline event. Nothing is
-- charged. The open invoices of periods that will not run (period_start at or after the period
-- end, renews_at) and an opening invoice still without a period are voided in the same statement
-- (void_reason 'subscription_cancelled', voided_by NULL, as payments/void_for_subscription.sql);
-- the invoice of the period that ran stays owed. The subscription rows are locked by `due` before
-- the invoice UPDATE can match them: the payments lock order (subscription, then invoice).
-- %(now)s is the job clock, so a test clock and production use the same statement.
WITH due AS (
    SELECT id, status AS from_status, pending_plan, renews_at
    FROM public.subscriptions
    WHERE client_id = %(client_id)s
      AND status = 'active'
      AND cancel_at_period_end
      AND renews_at <= %(now)s::timestamptz
    ORDER BY renews_at, id
    FOR UPDATE SKIP LOCKED
    LIMIT %(batch_size)s
), changed AS (
    UPDATE public.subscriptions s
    SET status = 'cancelled', cancelled_at = s.renews_at, cancel_at_period_end = false,
        pending_plan = NULL, pending_effective_at = NULL, version = s.version + 1
    FROM due
    WHERE s.client_id = %(client_id)s AND s.id = due.id
    RETURNING s.id, due.from_status, s.status AS to_status, due.pending_plan
), logged AS (
    INSERT INTO public.subscription_events
        (client_id, subscription_id, actor, action, from_status, to_status, payload)
    SELECT %(client_id)s, id, 'system:subscriptions_due', 'cancelled_at_period_end',
           from_status, to_status, jsonb_build_object('cleared_pending_plan', pending_plan)
    FROM changed
    RETURNING id
), voided AS (
    UPDATE public.invoices i
    SET status = 'void', void_reason = 'subscription_cancelled', voided_at = %(now)s::timestamptz
    FROM due
    WHERE i.client_id = %(client_id)s AND i.subscription_id = due.id
      AND i.status IN ('issued', 'overdue', 'proof_submitted', 'cash_pending')
      AND (i.period_start IS NULL OR i.period_start >= due.renews_at)
    RETURNING i.id
)
SELECT count(*) AS cancelled FROM logged;
