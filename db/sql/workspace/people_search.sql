SELECT employee_id, display_name, job_title, work_email
FROM public.company_directory
WHERE client_id = %s AND (display_name ILIKE %s OR work_email ILIKE %s)
ORDER BY display_name, employee_id
LIMIT 10
