SELECT EXISTS(SELECT 1 FROM public.hr_shifts WHERE client_id=%s AND employee_id=%s AND status='scheduled' AND starts_at<%s AND ends_at>%s) AS conflict;
