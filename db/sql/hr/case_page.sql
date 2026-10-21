SELECT * FROM public.hr_cases WHERE client_id=%s AND (%s::uuid IS NULL OR id>%s::uuid)
AND (public.hr_case_access(client_id,domain,'read',employee_id)
     OR public.hr_case_access(client_id,domain,'decide',employee_id)) ORDER BY id LIMIT %s;
