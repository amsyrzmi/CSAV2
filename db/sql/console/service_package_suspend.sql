UPDATE public.console_service_packages
SET status='suspended', note=COALESCE(%s, note), updated_at=now()
WHERE client_id=%s AND package=%s
RETURNING *;
