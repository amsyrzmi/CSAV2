SELECT id,kind,resource_id,created_at,read_at,due_on,subject FROM public.hr_notifications WHERE client_id=%s ORDER BY created_at DESC LIMIT 100;
