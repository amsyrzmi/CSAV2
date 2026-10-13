UPDATE public.tickets
SET status = %s,
    closed_at = CASE WHEN %s THEN now() ELSE NULL END,
    updated_at = now()
WHERE client_id = %s
  AND id = %s
RETURNING id;
