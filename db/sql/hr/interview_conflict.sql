SELECT EXISTS(SELECT 1 FROM public.hr_interviews WHERE client_id=%s AND status='scheduled' AND (interviewer_id=%s OR candidate_id=%s) AND starts_at<%s AND ends_at>%s) AS conflict;
