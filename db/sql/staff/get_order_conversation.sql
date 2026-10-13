SELECT o.contact_id, c.id AS conversation_id
FROM public.orders AS o
JOIN public.conversations AS c
  ON c.client_id = o.client_id AND c.contact_id = o.contact_id AND c.channel = 'telegram'
WHERE o.client_id = %s
  AND o.id = %s
ORDER BY c.opened_at DESC
LIMIT 1;
