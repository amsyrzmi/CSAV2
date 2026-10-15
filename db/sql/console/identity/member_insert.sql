INSERT INTO console_memberships (user_id,client_id,role) VALUES (%s,%s,%s)
ON CONFLICT (user_id,client_id) DO UPDATE SET role=excluded.role,is_enabled=true,updated_at=now();
