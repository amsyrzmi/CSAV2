INSERT INTO console_users (email, display_name, password_hash, is_platform_admin, must_change_password)
VALUES (%s, %s, %s, %s, %s) RETURNING *;
