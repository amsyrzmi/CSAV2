-- What staff should look at, at most %(limit)s invoices of each kind, each kind oldest `since`
-- first, kinds in this order:
--   proof_submitted: open proof_submitted invoices; since = claimed_at (issued_at if never set).
--   needs_nudge: issued or overdue invoices whose newest reminder row (latest created_at, then
--     highest step) is no_channel; since = that row's created_at. One row per invoice.
--   reminder_failed: open invoices with a failed reminder created in the last 14 days; since = the
--     newest such row. One row per invoice.
-- A paid, void or draft invoice never appears. Columns as in invoices_list.sql, plus kind, since.
WITH proof AS (
    SELECT 1 AS rank, 'proof_submitted'::text AS kind, i.id AS invoice_id,
           coalesce(i.claimed_at, i.issued_at) AS since
    FROM public.invoices i
    WHERE i.client_id = %(client_id)s AND i.status = 'proof_submitted'
    ORDER BY since, i.id
    LIMIT %(limit)s
), nudge AS (
    SELECT 2 AS rank, 'needs_nudge'::text AS kind, i.id AS invoice_id, last.created_at AS since
    FROM public.invoices i
    CROSS JOIN LATERAL (
        SELECT r.status, r.created_at FROM public.invoice_reminders r
        WHERE r.client_id = i.client_id AND r.invoice_id = i.id
        ORDER BY r.created_at DESC, r.step DESC
        LIMIT 1
    ) last
    WHERE i.client_id = %(client_id)s AND i.status IN ('issued', 'overdue')
      AND last.status = 'no_channel'
    ORDER BY since, i.id
    LIMIT %(limit)s
), failed AS (
    SELECT 3 AS rank, 'reminder_failed'::text AS kind, i.id AS invoice_id,
           max(r.created_at) AS since
    FROM public.invoices i
    JOIN public.invoice_reminders r ON r.client_id = i.client_id AND r.invoice_id = i.id
    WHERE i.client_id = %(client_id)s
      AND i.status IN ('issued', 'overdue', 'proof_submitted', 'cash_pending')
      AND r.status = 'failed' AND r.created_at > now() - interval '14 days'
    GROUP BY i.id
    ORDER BY since, i.id
    LIMIT %(limit)s
), picked AS (
    SELECT * FROM proof UNION ALL SELECT * FROM nudge UNION ALL SELECT * FROM failed
)
SELECT p.kind, p.since,
       i.id, i.reference, i.status, i.amount, i.currency, i.issued_at, i.due_at, i.paid_at,
       i.period_start, i.period_end, i.subscription_id,
       i.line_items -> 0 ->> 'description' AS plan_name, i.claim_note, i.claimed_at,
       i.method_kind, i.void_reason, i.voided_at, i.reminded_at,
       c.id AS contact_id, c.full_name AS contact_full_name,
       ARRAY(SELECT DISTINCT ci.channel FROM public.contact_identities ci
              WHERE ci.client_id = i.client_id AND ci.contact_id = c.id
              ORDER BY ci.channel) AS contact_channels
FROM picked p
JOIN public.invoices i ON i.client_id = %(client_id)s AND i.id = p.invoice_id
LEFT JOIN public.contacts c ON c.client_id = i.client_id AND c.id = i.contact_id
ORDER BY p.rank, p.since, i.id;
