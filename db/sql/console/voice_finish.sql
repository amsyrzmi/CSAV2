UPDATE console_voice_jobs SET status=%s, transcript=%s, error_code=%s
WHERE id=%s AND client_id=%s AND status='processing'
RETURNING *;
