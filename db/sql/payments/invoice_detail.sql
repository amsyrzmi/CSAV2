-- One invoice of the company for the staff detail, with its ledger row when it has one (at most
-- one: payments_one_per_invoice). payment_created_by is a console user id; the route looks the
-- name up on the identity pool (csa_app cannot read console_users). Contact columns as in
-- invoices_list.sql.
SELECT i.id, i.reference, i.status, i.amount, i.currency, i.issued_at, i.due_at, i.paid_at,
       i.period_start, i.period_end, i.subscription_id,
       i.line_items -> 0 ->> 'description' AS plan_name, i.claim_note, i.claimed_at,
       i.method_kind, i.void_reason, i.voided_at, i.reminded_at,
       c.id AS contact_id, c.full_name AS contact_full_name,
       ARRAY(SELECT DISTINCT ci.channel FROM public.contact_identities ci
              WHERE ci.client_id = i.client_id AND ci.contact_id = c.id
              ORDER BY ci.channel) AS contact_channels,
       p.id AS payment_id, p.amount AS payment_amount, p.currency AS payment_currency,
       p.received_at AS payment_received_at, p.method_kind AS payment_method_kind,
       p.source AS payment_source, p.matched_by AS payment_matched_by,
       p.created_by AS payment_created_by, p.note AS payment_note,
       p.created_at AS payment_created_at
FROM public.invoices i
LEFT JOIN public.contacts c ON c.client_id = i.client_id AND c.id = i.contact_id
LEFT JOIN public.payments p ON p.client_id = i.client_id AND p.invoice_id = i.id
WHERE i.client_id = %(client_id)s AND i.id = %(invoice_id)s;
