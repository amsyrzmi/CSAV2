SELECT count(*)::integer AS n FROM public.hr_cases c WHERE c.client_id=%s AND c.status='pending' AND public.hr_case_access(c.client_id,c.domain,'decide',c.employee_id);
