INSERT INTO public.console_voice_utterances
    (session_id,utterance_id,client_id,user_id,conversation_id,request_key,fingerprint,turn_id,trace_id)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *;
