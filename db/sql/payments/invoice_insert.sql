-- Issues one invoice ('issued'). The caller holds the subscription's lock (subscription_for_
-- invoice.sql) and runs this in a savepoint: a reference collision (invoices_reference_uq) or an id
-- collision (invoices_pkey) raises, and the caller draws again. A second invoice for the same
-- (company, subscription, period_start), the opening invoice's NULL period_start included and a
-- void invoice included, is not an error: invoices_subscription_period_uq makes it DO NOTHING and
-- no row comes back. amount is numeric(12,2) from a Decimal, never a float.
INSERT INTO public.invoices
    (id, client_id, contact_id, subscription_id, reference, status, amount, currency, issued_at,
     due_at, period_start, period_end, line_items)
VALUES (%(id)s, %(client_id)s, %(contact_id)s, %(subscription_id)s, %(reference)s, 'issued',
        %(amount)s, %(currency)s, %(issued_at)s, %(due_at)s, %(period_start)s, %(period_end)s,
        %(line_items)s)
ON CONFLICT (client_id, subscription_id, period_start) WHERE subscription_id IS NOT NULL
    DO NOTHING
RETURNING id, client_id, contact_id, subscription_id, reference, status, amount, currency,
          issued_at, due_at, paid_at, period_start, period_end,
          line_items -> 0 ->> 'description' AS plan_name, claim_note, claimed_at, method_kind,
          false AS has_payment;
