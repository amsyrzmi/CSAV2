DELETE FROM console_login_attempts WHERE window_started_at<now()-make_interval(secs => %s);
