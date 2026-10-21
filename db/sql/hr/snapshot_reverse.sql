UPDATE public.hr_payroll_snapshots SET status='reversed',reversal_reason=%s,version=version+1 WHERE client_id=%s AND case_id=%s AND status='approved' AND version=%s RETURNING version;
