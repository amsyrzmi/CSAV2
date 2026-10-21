SELECT subject_id,request_key,operation,details,occurred_at FROM public.hr_authority_events WHERE client_id=%s ORDER BY occurred_at DESC LIMIT 200;
