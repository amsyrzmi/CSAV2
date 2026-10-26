SELECT session_id,fingerprint,plan FROM public.hr_chat_plans WHERE client_id=%s AND subject_id=%s AND request_key=%s;
