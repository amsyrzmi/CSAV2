UPDATE public.messages
SET ui = %s
WHERE id = %s
  AND client_id = %s
  AND direction = 'outbound'
  AND ui IS NULL
RETURNING id;
