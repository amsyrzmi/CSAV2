SELECT id,employee_id,status,starts_at,ends_at FROM public.hr_shifts WHERE client_id=%s ORDER BY starts_at DESC LIMIT 200;
