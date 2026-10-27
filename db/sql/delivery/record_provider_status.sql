UPDATE public.outbound_deliveries
SET provider_status = %s
WHERE client_id = %s
  AND provider_message_id = %s
  AND (
    provider_status IS NULL
    OR %s = 'failed'
    OR (CASE provider_status WHEN 'sent' THEN 1 WHEN 'delivered' THEN 2 WHEN 'read' THEN 3 ELSE 0 END)
       < (CASE %s WHEN 'sent' THEN 1 WHEN 'delivered' THEN 2 WHEN 'read' THEN 3 ELSE 0 END)
  )
RETURNING id
