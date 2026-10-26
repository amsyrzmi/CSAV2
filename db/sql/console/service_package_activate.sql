INSERT INTO public.console_service_packages
    (client_id, package, status, activated_by, activated_at, reference, note)
VALUES (%s, %s, 'active', %s, now(), %s, %s)
ON CONFLICT (client_id, package) DO UPDATE
SET status='active', activated_by=EXCLUDED.activated_by, activated_at=now(),
    reference=EXCLUDED.reference, note=EXCLUDED.note, updated_at=now()
RETURNING *;
