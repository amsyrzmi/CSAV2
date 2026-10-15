INSERT INTO console_sessions (user_id,token_hash,csrf_hash,client_id,expires_at)
VALUES (%s,%s,%s,%s,now() + make_interval(secs => %s)) RETURNING id;
