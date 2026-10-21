SELECT id,candidate_id,interviewer_id,starts_at,ends_at,timezone,delivery,status FROM public.hr_interviews WHERE client_id=%s ORDER BY starts_at DESC LIMIT 200;
