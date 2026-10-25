UPDATE console_voice_jobs SET
    status=CASE WHEN created_at < now()-interval '1 hour' THEN 'expired' ELSE 'timeout' END,
    transcript=NULL,
    error_code=CASE WHEN created_at < now()-interval '1 hour' THEN 'recording_expired' ELSE 'transcription_timeout' END
WHERE client_id=%s AND user_id=%s
  AND ((status='processing' AND created_at < now()-interval '90 seconds')
    OR (status <> 'expired' AND created_at < now()-interval '1 hour'));
