INSERT INTO public.console_service_packages (client_id, package, status, requested_by, requested_at)
VALUES (%s, %s, 'requested', %s, now())
ON CONFLICT (client_id, package) DO UPDATE
SET status='requested', requested_by=EXCLUDED.requested_by, requested_at=now(), updated_at=now()
WHERE console_service_packages.status <> 'active'
RETURNING *;
