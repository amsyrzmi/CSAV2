INSERT INTO public.console_voice_sessions
    (id,client_id,user_id,login_session_id,conversation_id,request_key,language,expires_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,LEAST(now()+interval '15 minutes',%s)) RETURNING *;
