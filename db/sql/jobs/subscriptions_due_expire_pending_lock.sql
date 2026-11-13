-- Rule 5 of the subscriptions_due job, first statement: claims the pending (approved, never started
-- or paid) subscriptions older than the company's subscription_settings.pending_expiry_days (no
-- row means 14; 0 means never expire), oldest first, at most %(batch_size)s, FOR UPDATE SKIP
-- LOCKED, and returns their ids. Age counts from started_at, the approval time of a pending row.
-- A subscription with a proof_submitted or cash_pending invoice (the customer said they paid, or
-- cash is waiting) is not claimed. subscriptions_due_expire_pending.sql then reads the invoices
-- again in a statement of its own: a customer's claim locks the subscription before its invoice
-- (the payments lock order), so a claim that committed before this lock was taken is visible to
-- that second statement, and one that comes later waits for this transaction.
-- %(now)s is the job clock, so a test clock and production use the same statement.
WITH setting AS (
    SELECT coalesce(
        (SELECT pending_expiry_days FROM public.subscription_settings
         WHERE client_id = %(client_id)s),
        14) AS days
)
SELECT s.id
FROM public.subscriptions s, setting
WHERE s.client_id = %(client_id)s
  AND s.status = 'pending'
  AND setting.days > 0
  AND s.started_at < %(now)s::timestamptz - make_interval(days => setting.days)
  AND NOT EXISTS (
      SELECT 1 FROM public.invoices i
      WHERE i.client_id = s.client_id AND i.subscription_id = s.id
        AND i.status IN ('proof_submitted', 'cash_pending'))
ORDER BY s.started_at, s.id
FOR UPDATE OF s SKIP LOCKED
LIMIT %(batch_size)s;
