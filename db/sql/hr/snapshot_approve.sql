UPDATE public.hr_payroll_snapshots SET status='approved',reviewed_by=%s,version=version+1 WHERE client_id=%s AND case_id=%s AND status='prepared' RETURNING version;
