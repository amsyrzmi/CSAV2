-- Step 3 of the payments_due job: the company's issued invoices whose due time has passed
-- (due_at < %(now)s) become overdue, oldest due first, at most %(batch_size)s per run. An invoice
-- the customer said they paid (proof_submitted) or that waits for cash (cash_pending) is never
-- marked. The rows are locked FOR UPDATE SKIP LOCKED: an invoice that staff are paying or voiding,
-- or a customer is claiming, is skipped (the next run sees its new state), so this statement never
-- waits on a lock and cannot deadlock with the payments lock order (subscription, then invoice).
-- A row changed and committed meanwhile is re-checked against status = 'issued' by the lock.
WITH due AS (
    SELECT i.id
    FROM public.invoices i
    WHERE i.client_id = %(client_id)s
      AND i.status = 'issued'
      AND i.due_at < %(now)s::timestamptz
    ORDER BY i.due_at, i.id
    FOR UPDATE SKIP LOCKED
    LIMIT %(batch_size)s
), marked AS (
    UPDATE public.invoices i
    SET status = 'overdue'
    FROM due
    WHERE i.client_id = %(client_id)s AND i.id = due.id AND i.status = 'issued'
    RETURNING i.id
)
SELECT count(*) AS overdue FROM marked;
