INSERT INTO console_voice_jobs (id, client_id, user_id, conversation_id, request_key, fingerprint)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (client_id, user_id, conversation_id, request_key) DO NOTHING
RETURNING *;
