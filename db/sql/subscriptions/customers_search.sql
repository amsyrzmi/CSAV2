-- Customers (contacts) to put on a plan. %(like)s is an already-escaped ILIKE pattern or NULL
-- (no term: everyone, by name); %(digits)s holds only 0-9 (empty = no phone match).
SELECT c.id, c.full_name, c.phone, c.email
FROM public.contacts c
WHERE c.client_id = %(client_id)s
  AND (%(like)s::text IS NULL
       OR c.full_name ILIKE %(like)s
       OR c.email ILIKE %(like)s
       OR (%(digits)s <> '' AND regexp_replace(coalesce(c.phone, ''), '\D', '', 'g')
           LIKE '%%' || %(digits)s || '%%'))
ORDER BY c.full_name NULLS LAST, c.id
LIMIT %(limit)s;
