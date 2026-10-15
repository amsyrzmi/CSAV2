UPDATE console_users SET must_change_password=true,updated_at=now() WHERE id=%s;
