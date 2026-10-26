SELECT employee_id, manager_employee_id, manager_display_name, manager_job_title,
       manager_work_email
FROM public.company_directory_me
WHERE client_id = %s
