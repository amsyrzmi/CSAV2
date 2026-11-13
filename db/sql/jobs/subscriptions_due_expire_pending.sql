-- Rule 5 of the subscriptions_due job, second statement: the pending subscriptions that
-- subscriptions_due_expire_pending_lock.sql claimed and locked (%(ids)s) are cancelled, effective
-- now, unless one is no longer pending or now has a proof_submitted or cash_pending invoice (read
-- here with this statement's own, later snapshot, so a customer's claim that committed while the
-- claim statement ran is seen: such a subscription is kept). Active and paused rows are never
-- touched. The sign-up request that points at the subscription is marked expired in the same
-- statement, only while it is still approved, and the subscription's open invoices (its opening
-- invoice) are voided with void_reason 'subscription_expired' and voided_by NULL. Version bumps,
-- and each row gets one 'pending_expired' timeline event. Nothing is charged and nobody is
-- messaged. %(now)s is the job clock, so a test clock and production use the same statement.
WITH due AS (
    SELECT s.id, s.status AS from_status
    FROM public.subscriptions s
    WHERE s.client_id = %(client_id)s
      AND s.id = ANY(%(ids)s::uuid[])
      AND s.status = 'pending'
      AND NOT EXISTS (
          SELECT 1 FROM public.invoices i
          WHERE i.client_id = s.client_id AND i.subscription_id = s.id
            AND i.status IN ('proof_submitted', 'cash_pending'))
    FOR UPDATE OF s
), changed AS (
    UPDATE public.subscriptions s
    SET status = 'cancelled', cancelled_at = %(now)s::timestamptz, version = s.version + 1
    FROM due
    WHERE s.client_id = %(client_id)s AND s.id = due.id
    RETURNING s.id, due.from_status, s.status AS to_status
), logged AS (
    INSERT INTO public.subscription_events
        (client_id, subscription_id, actor, action, from_status, to_status, payload)
    SELECT %(client_id)s, id, 'system:subscriptions_due', 'pending_expired',
           from_status, to_status, '{}'::jsonb
    FROM changed
    RETURNING id
), signups AS (
    UPDATE public.signup_requests r
    SET status = 'expired'
    FROM changed
    WHERE r.client_id = %(client_id)s AND r.subscription_id = changed.id AND r.status = 'approved'
    RETURNING r.id
), voided AS (
    UPDATE public.invoices i
    SET status = 'void', void_reason = 'subscription_expired', voided_at = %(now)s::timestamptz
    FROM changed
    WHERE i.client_id = %(client_id)s AND i.subscription_id = changed.id
      AND i.status IN ('issued', 'overdue', 'proof_submitted', 'cash_pending')
    RETURNING i.id
)
SELECT count(*) AS pending_expired FROM logged;
