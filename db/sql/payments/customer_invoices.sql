-- A customer's own invoices as they may see them: open ones (%(open)s, the OPEN_STATUSES list)
-- first, soonest due first, then paid ones from the last 60 days, newest first. A paid invoice is
-- shown only when the ledger holds its payment (a 'paid' row without one is an old seed). Never a
-- draft, a void invoice or another contact's. %(reference)s, when set, is a normalised reference
-- and narrows within this contact's invoices only.
SELECT i.id, i.client_id, i.contact_id, i.subscription_id, i.reference, i.status, i.amount,
       i.currency, i.issued_at, i.due_at, i.paid_at, i.period_start, i.period_end,
       i.line_items -> 0 ->> 'description' AS plan_name, i.claim_note, i.claimed_at,
       i.method_kind
FROM public.invoices i
WHERE i.client_id = %(client_id)s AND i.contact_id = %(contact_id)s
  AND (%(reference)s::text IS NULL OR i.reference = %(reference)s::text)
  AND (i.status = ANY(%(open)s::text[])
       OR (i.status = 'paid' AND i.paid_at >= now() - interval '60 days'
           AND EXISTS (SELECT 1 FROM public.payments p
                        WHERE p.client_id = i.client_id AND p.invoice_id = i.id)))
ORDER BY i.status = 'paid',
         CASE WHEN i.status <> 'paid' THEN i.due_at END NULLS LAST,
         i.paid_at DESC NULLS LAST, i.issued_at DESC, i.id
LIMIT %(limit)s;
