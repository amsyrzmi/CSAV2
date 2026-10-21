SELECT id AS confirmation_id,session_id,request_id,command,fingerprint,created_at,expires_at,consumed_at
FROM public.hr_agent_confirmations WHERE client_id=%s AND id=%s FOR UPDATE;
