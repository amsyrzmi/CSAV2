SELECT DISTINCT lower(work_email) AS email FROM public.company_directory
WHERE client_id=%s AND work_email IS NOT NULL AND lower(work_email)=ANY(%s);
