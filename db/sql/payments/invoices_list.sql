-- Staff invoice list, newest issued first, keyset on (issued_at, id): %(before)s is the id of the
-- last invoice of the previous page (NULL for the first page; an id the company does not have
-- gives an empty page). %(statuses)s is the filter's status list. %(has_q)s says whether a search
-- term was given; it then matches the reference %(reference)s (already normalised, NULL when the
-- term cannot be one), the contact's name by %(like)s (an escaped ILIKE pattern) or, when
-- %(digits)s is not empty, the digits of the contact's phone. contact_channels is the contact's
-- distinct identity channels (as in contacts/links_list.sql); no phone, email or address leaves.
SELECT i.id, i.reference, i.status, i.amount, i.currency, i.issued_at, i.due_at, i.paid_at,
       i.period_start, i.period_end, i.subscription_id,
       i.line_items -> 0 ->> 'description' AS plan_name, i.claim_note, i.claimed_at,
       i.method_kind, i.void_reason, i.voided_at, i.reminded_at,
       c.id AS contact_id, c.full_name AS contact_full_name,
       ARRAY(SELECT DISTINCT ci.channel FROM public.contact_identities ci
              WHERE ci.client_id = i.client_id AND ci.contact_id = c.id
              ORDER BY ci.channel) AS contact_channels
FROM public.invoices i
LEFT JOIN public.contacts c ON c.client_id = i.client_id AND c.id = i.contact_id
WHERE i.client_id = %(client_id)s
  AND i.status = ANY(%(statuses)s::text[])
  AND (NOT %(has_q)s::boolean
       OR i.reference = %(reference)s::text
       OR c.full_name ILIKE %(like)s::text
       OR (%(digits)s::text <> '' AND regexp_replace(coalesce(c.phone, ''), '\D', '', 'g')
           LIKE '%%' || %(digits)s::text || '%%'))
  AND (%(before)s::text IS NULL OR (i.issued_at, i.id) <
       (SELECT b.issued_at, b.id FROM public.invoices b
         WHERE b.client_id = %(client_id)s AND b.id = %(before)s::text))
ORDER BY i.issued_at DESC, i.id DESC
LIMIT %(limit)s;
