UPDATE public.contacts
SET locale = %s
WHERE id = %s
  AND client_id = %s
  AND locale IS DISTINCT FROM %s
RETURNING id;
