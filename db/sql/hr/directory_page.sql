SELECT id FROM public.hr_employees
WHERE client_id=%s AND (%s::uuid IS NULL OR id>%s::uuid)
AND public.hr_resource_visible(client_id,id,'directory') ORDER BY id LIMIT %s;
