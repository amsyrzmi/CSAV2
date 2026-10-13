SELECT id, full_name
FROM public.contacts
WHERE client_id = %s
  AND telegram_id = %s
