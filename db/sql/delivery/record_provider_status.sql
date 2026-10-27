UPDATE public.outbound_deliveries
SET provider_status = %(status)s,
    provider_error = CASE WHEN %(status)s = 'failed' THEN %(error_code)s ELSE provider_error END
WHERE client_id = %(client_id)s
  AND provider_message_id = %(provider_message_id)s
  AND (
    provider_status IS NULL
    OR %(status)s = 'failed'
    OR (CASE provider_status
          WHEN 'sent' THEN 1 WHEN 'delivered' THEN 2 WHEN 'read' THEN 3 WHEN 'failed' THEN 4
          ELSE 0 END)
       < (CASE %(status)s
            WHEN 'sent' THEN 1 WHEN 'delivered' THEN 2 WHEN 'read' THEN 3 WHEN 'failed' THEN 4
            ELSE 0 END)
  )
RETURNING id
