INSERT INTO console_tokens (user_id,client_id,role,kind,token_hash,expires_at,created_by)
VALUES (%s,%s,%s,%s,%s,now()+make_interval(secs => %s),%s) RETURNING id,expires_at;
