INSERT INTO public.hr_agent_confirmations
    (client_id,id,subject_id,session_id,request_id,request_key,command,fingerprint,created_at,expires_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);
