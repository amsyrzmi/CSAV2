SELECT EXISTS(SELECT 1 FROM public.hr_cases WHERE client_id=%s AND employee_id=%s
    AND domain='attendance' AND status='approved' AND payload->>'correction_of'=%s) AS used;
