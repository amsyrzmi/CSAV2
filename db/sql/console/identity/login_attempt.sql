INSERT INTO console_login_attempts (bucket,attempts) VALUES (%s,1)
ON CONFLICT (bucket) DO UPDATE SET attempts=CASE WHEN console_login_attempts.window_started_at<now()-make_interval(secs => %s)
THEN 1 ELSE console_login_attempts.attempts+1 END, window_started_at=CASE
WHEN console_login_attempts.window_started_at<now()-make_interval(secs => %s) THEN now() ELSE console_login_attempts.window_started_at END
RETURNING attempts;
