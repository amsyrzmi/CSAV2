SELECT id
FROM public.console_proposals
WHERE client_id = %s AND user_id = %s AND login_session_id = %s AND kind = 'training_submit'
  AND payload ->> 'request_id' = %s AND created_at >= %s
ORDER BY created_at, id
LIMIT 1;
