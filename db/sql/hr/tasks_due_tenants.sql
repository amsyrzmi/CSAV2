SELECT DISTINCT client_id FROM public.hr_tasks_due(NULL::uuid, %s::date) ORDER BY client_id;
