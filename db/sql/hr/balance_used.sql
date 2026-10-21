SELECT coalesce(sum(days),0) AS used FROM public.hr_leave_usage WHERE client_id=%s AND employee_id=%s AND year=%s AND leave_type=%s;
