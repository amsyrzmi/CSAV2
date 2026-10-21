SELECT id,payload,reviewed_by FROM public.hr_payroll_rules WHERE client_id=%s ORDER BY id LIMIT 100;
