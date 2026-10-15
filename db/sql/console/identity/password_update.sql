UPDATE console_users SET password_hash=%s,must_change_password=false,updated_at=now() WHERE id=%s;
